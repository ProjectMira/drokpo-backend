# Tech Stack

This document explains every backend service the Changsa app is built on, what it's used for, and why it was chosen over the alternatives. Code references point at the actual files in this repo.

## Summary table

| Concern | Service | Where it's used |
|---|---|---|
| API framework | FastAPI (Python) | [backend/app/main.py](../backend/app/main.py) |
| Compute / hosting for the API | Cloud Run | [backend/Dockerfile](../backend/Dockerfile), rewrite in [firebase.json](../firebase.json) |
| Static entry point / custom domain | Firebase Hosting | [firebase.json](../firebase.json) |
| User accounts & sessions | Firebase Authentication | [backend/app/dependencies.py](../backend/app/dependencies.py) |
| Primary database | Cloud Firestore (NoSQL) | [backend/app/firebase.py](../backend/app/firebase.py), [firestore.rules](../firestore.rules) |
| Photo storage | Firebase Storage | [storage.rules](../storage.rules), [backend/app/services/storage.py](../backend/app/services/storage.py) |
| Push notifications | Firebase Cloud Messaging (FCM) | [functions/main.py](../functions/main.py) |
| Event-driven background jobs | Cloud Functions for Firebase (2nd gen, Python) | [functions/main.py](../functions/main.py) |
| Geo "nearby" search | Geohashing (`pygeohash`) on top of Firestore | [backend/app/services/geo.py](../backend/app/services/geo.py) |

---

## FastAPI

The application-logic layer: onboarding, profile CRUD, feed generation, swipe/match logic, block/report. FastAPI was chosen because it gives request validation (Pydantic models), automatic OpenAPI docs at `/docs`, and async support, without locking business logic inside Cloud Functions.

Every request is authenticated the same way — see [backend/app/dependencies.py](../backend/app/dependencies.py): the client sends `Authorization: Bearer <Firebase ID token>`, and `get_current_uid()` verifies it against Firebase Auth via the Admin SDK before any route logic runs.

## Cloud Run (deployment target)

You asked for "Firebase" as the deployment target. Firebase itself doesn't host a full ASGI app directly — it gives two options:

1. **Cloud Functions for Firebase (2nd gen, Python)** — runs a function per HTTP request; fine for small triggers, awkward for a full multi-route FastAPI app.
2. **Cloud Run** — runs the FastAPI app as a normal container, which is what a full backend needs. Cloud Run is part of the same Google Cloud project as your Firebase project, and Firebase Hosting can rewrite traffic straight to it.

We use **option 2**: [backend/Dockerfile](../backend/Dockerfile) builds the container, `gcloud run deploy` ships it, and the `hosting.rewrites` block in [firebase.json](../firebase.json) maps `/api/**` on your Firebase Hosting domain to that Cloud Run service. Hosting forwards the path *unstripped*, so FastAPI mounts every router under a matching `/api` prefix ([main.py](../backend/app/main.py)) — the full path of an endpoint is e.g. `GET /api/profile/me`, both through Hosting and against the Cloud Run URL directly. End result: it still deploys and serves through Firebase, just with a real container runtime instead of a function.

This deploy happens automatically on every push to `main` — see [DEPLOYMENT.md](DEPLOYMENT.md) for the GitHub Actions workflow and the one-time GCP setup it requires.

## Firebase Authentication

Handles sign-up/sign-in so the backend never touches passwords or OTP codes directly. Phone number + OTP is the recommended primary method for this app (matches how most dating apps and the target user base already verify identity); Google sign-in can be added as a secondary option — both are toggled in the Firebase console with no backend code changes, since the backend only ever deals with the verified ID token.

Flow:
1. Client signs in using the Firebase client SDK (phone/OTP or Google).
2. Client gets a short-lived **ID token** from Firebase Auth.
3. Client sends that token as a Bearer header on every API call.
4. `firebase_admin.auth.verify_id_token()` in [dependencies.py](../backend/app/dependencies.py) checks the token's signature and expiry and extracts the `uid` — this `uid` is what every Firestore document is keyed by.

No session table, no password storage, no refresh-token logic to build — Firebase Auth owns all of that.

## Database: Cloud Firestore (NoSQL) — not Data Connect (SQL)

Firebase actually offers two database products, and it's worth being explicit about why we picked one:

| | **Firestore (chosen)** | Firebase Data Connect (Cloud SQL/PostgreSQL) |
|---|---|---|
| Data model | Documents/collections, denormalized | Relational tables, joins, foreign keys |
| Real-time updates | Native — clients subscribe to a query/document and get live updates | Not real-time; polling or a separate layer needed |
| Client SDK access with security rules | Yes — clients can read/write directly under rule enforcement | No — access goes through GraphQL resolvers you write |
| Best fit here | Swipe feed, match documents, **chat messages** | Complex relational reporting/analytics |

Real-time listeners are the deciding factor: **chat only works the way it's designed** (client subscribes to `matches/{matchId}/messages` and gets new messages instantly, no socket server) because Firestore supports live queries and per-document security rules out of the box. A SQL backend would need a separate real-time layer (WebSockets, SSE, or a sync service) bolted on top to get the same behavior. If the app later needs heavy relational analytics (e.g. cohort/funnel reporting across millions of rows), Data Connect/Cloud SQL could be added *alongside* Firestore for that specific purpose — but it isn't part of the MVP.

**Collections in use** (full field-by-field schema, with exactly what's enforced vs. just conventional, is in [DATA_SCHEMA.md](DATA_SCHEMA.md); see [firestore.rules](../firestore.rules) for access control and [firestore.indexes.json](../firestore.indexes.json) for the composite indexes the queries need):

- `users/{uid}` — profile, preferences, photos array, geohash location. Clients can read only their own document and cannot write it at all at the rules level; the FastAPI backend uses the Admin SDK, which bypasses rules entirely, so every write (`create_profile`, `update_profile`, `add_photo` in [users.py](../backend/app/services/users.py)) happens server-side after validation — a client can't skip onboarding checks or spoof its location by writing the document directly.
- `users/{uid}/swipes/{targetUid}` — one doc per swipe, subcollection keyed by target for O(1) "have I already swiped on this person" checks. Rules deny all client access — swipes only exist because the backend wrote them, so a client can't fake a "like" from someone else.
- `matches/{matchId}` — deterministic ID (`sorted(uidA, uidB)` joined by `_`), created inside a Firestore **transaction** in [matching.py](../backend/app/services/matching.py) only when both sides have liked each other. The transaction reads the reverse swipe *before* writing anything (a Firestore requirement — all reads in a transaction must precede all writes), which also makes it safe against two simultaneous "like" swipes racing into duplicate matches.
- `matches/{matchId}/messages/{messageId}` — the chat thread. This is the one place where **clients write directly to Firestore** (not through FastAPI) — see the rules in [firestore.rules](../firestore.rules): a write is only allowed if the caller's uid is in the parent match's `users` array and `senderId` matches their own uid. Full design, message flow, and known gaps are in [CHAT_SYSTEM.md](CHAT_SYSTEM.md).
- `reports/{reportId}`, `blocks/{uid}/blockedUsers/{blockedUid}` — safety data, backend-write-only.

**Indexes**: Firestore requires a composite index whenever a query combines an equality filter with a range filter, or an array-contains with another filter. [firestore.indexes.json](../firestore.indexes.json) declares the two this app needs: `users` (status + geohash range, for the feed query) and `matches` (array-contains uid + status, for listing a user's active matches).

## Firebase Storage

Stores profile photos at `users/{uid}/photos/{photoId}.jpg`. The upload itself happens **client → Storage directly**, using the Firebase client SDK — not proxied through FastAPI — because the client is already authenticated with a Firebase ID token, and [storage.rules](../storage.rules) enforces `request.auth.uid == uid` on the path plus a 10MB size cap and an `image/*` content-type check. This avoids the backend having to generate and manage signed upload URLs.

The backend's only job (`backend/app/services/storage.py`) is to confirm the blob actually exists after upload before recording its path on the user's Firestore profile — see `POST /onboarding/photos/confirm` and `POST /profile/me/photos` in the routers. Reads are allowed for any signed-in user, since profile photos need to be visible to everyone in the swipe feed.

## Firebase Cloud Messaging (FCM) + Cloud Functions

Push notifications ("It's a match!", "New message") are handled outside the FastAPI request/response cycle, by Cloud Functions that trigger on Firestore document creation:

- `on_match_created` fires when a `matches/{matchId}` doc is created, looks up both users' stored FCM device tokens, and sends a match notification.
- `on_message_created` fires when a `matches/{matchId}/messages/{messageId}` doc is created (i.e. right after a client writes a chat message directly to Firestore), updates the parent match's `lastMessage`/`unreadCount` fields, and pushes a notification to the recipient.

See [functions/main.py](../functions/main.py). These are deployed and scaled independently of the Cloud Run API (`firebase deploy --only functions`), since they're reacting to database events rather than serving HTTP requests.

## Geohashing for the feed

Firestore has no native "find documents within N km" query. Each user's `location` is stored with a `geohash` string (via `pygeohash`, see [geo.py](../backend/app/services/geo.py)), and the feed query in [users.py](../backend/app/services/users.py) does a prefix range query on a 5-character geohash (~4.9km cells). This is a known-limitation approach — candidates just across a cell boundary can be missed — documented in the code as a deliberate MVP simplification; a proper radius search (geohash neighbor expansion, or a dedicated geo index) can replace it later without changing how location is stored.
