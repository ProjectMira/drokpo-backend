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
  └── swipes/{targetUid}

matches/{matchId}
  └── messages/{messageId}

reports/{reportId}

blocks/{uid}
  └── blockedUsers/{blockedUid}
```

There is no top-level `chats` or `likes` collection — swipes live under the swiping user, and messages live under the match they belong to, since both are always queried in that scoped context.

---

## `users/{uid}`

The profile document. One per Firebase Auth account, created by `POST /onboarding` ([users.py](../backend/app/services/users.py) `create_profile`) and mutated by `PATCH /profile/me` and the photo endpoints.

| Field | Type | Written by | Notes |
|---|---|---|---|
| `displayName` | string | onboarding, profile update | |
| `dob` | string | onboarding only | Plain ISO date string (`"1998-04-12"`), not a Firestore Timestamp — never overwritten after onboarding |
| `gender` | string | onboarding only | No edit endpoint currently exposes this after onboarding |
| `seekingGenders` | array\<string\> | onboarding, profile update | The single source of truth for gender intent — drives the feed's gender filter in `get_candidates` |
| `bio` | string | onboarding, profile update | |
| `occupation` | string | profile update only | Not collected at onboarding, only editable afterward |
| `education` | string | profile update only | Same as `occupation` |
| `region` | string | onboarding, profile update | Free text, e.g. "U-Tsang", "Kham", "Amdo", or a diaspora city |
| `languages` | array\<string\> | onboarding, profile update | |
| `location` | map `{ lat: number, lng: number, geohash: string }` | onboarding only | `geohash` is computed server-side ([geo.py](../backend/app/services/geo.py)); there's currently no endpoint to update location after onboarding |
| `preferences` | map `{ ageMin: number, ageMax: number, distanceKm: number }` | onboarding, profile update | `ageMin`/`ageMax` drive the feed's age filter. Partial updates merge via dotted field paths (`preferences.ageMin`), so sending only `distanceKm` does not reset the others. Gender intent lives in `seekingGenders`, not here |
| `photos` | array of maps `{ storagePath: string, order: number }` | backend, via `add_photo`/`remove_photo` | Capped at 6 (`MAX_PHOTOS` in [users.py](../backend/app/services/users.py)); `add_photo` rejects further attaches with a 400. No `url` field is stored — the client derives a download URL from `storagePath` itself via the Storage SDK, since [storage.rules](../storage.rules) allows any signed-in user to read photo blobs |
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

**Access rule:** `allow read, write: if false` — entirely backend-only. A client can never read or forge a swipe directly; every swipe is created by [matching.py](../backend/app/services/matching.py) as part of the swipe transaction.

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
| `unreadCount` | map `{ [uid]: number }` | Cloud Function (`on_message_created`) increments; nothing currently resets it | See Known Gaps below |

**Access rule:** clients can `read` a match only if their uid is in its `users` array; `write` is `false` — only the backend creates/updates it.

### `matches/{matchId}/messages/{messageId}`

The one collection clients write to **directly**, bypassing FastAPI entirely — see [CHAT_SYSTEM.md](CHAT_SYSTEM.md) for the full rationale. Document ID is Firestore's auto-generated ID.

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

## Composite indexes

Firestore requires an explicit composite index whenever a query combines an equality filter with a range filter, or `array-contains` with another filter. Declared in [firestore.indexes.json](../firestore.indexes.json):

| Collection | Fields | Powers |
|---|---|---|
| `users` | `status` (==) + `location.geohash` (range) | The feed candidate query in `get_candidates` |
| `matches` | `users` (array-contains) + `status` (==) | `GET /matches` listing a user's active matches |

---

## Known gaps in the current schema

These are things the schema *implies* but the code doesn't fully implement yet — flagging them here rather than in the code, since they're schema-level decisions as much as bugs:

1. **`matches.unreadCount` is incremented but never reset.** There's no endpoint or client-direct rule allowing a user to zero out their own unread count when they open a thread.
2. **`messages.readAt` is reserved but unreachable** — the rules block all updates to a message, so no read-receipt mechanism can write to it as the schema currently stands. It would need either a narrowly-scoped update rule (recipient-only, single-field) or moving read state onto the `matches` doc instead of per-message.
3. **`gender` and `location` have no post-onboarding edit path** (`seekingGenders` is now editable via `PATCH /profile/me`) — worth deciding whether that's intentional (e.g. to prevent gaming the system) or just not built yet.
4. **`preferences.distanceKm` is stored but the geohash cell size is fixed** at ~4.9km regardless of the chosen radius — the feed neither tightens nor widens with it (see the geohashing note in [TECH_STACK.md](TECH_STACK.md)).

Previously listed gaps now closed: `fcmTokens` is writable via `POST /profile/me/fcm-tokens`; blocks are enforced in both the feed and the swipe path; messaging locks when a match is unmatched; photos are capped at 6.
