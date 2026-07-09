# Chat / Messaging System

This is the part of the design that avoids building a WebSocket server. It leans entirely on Firestore's real-time listeners and security rules, with FastAPI only acting as the gatekeeper that decides *whether a chat is allowed to exist at all*.

## The core idea

There are two very different kinds of writes in this app, and they're handled by two different paths:

| Write | Who performs it | Why |
|---|---|---|
| Creating a match | FastAPI, via the Admin SDK, inside a transaction | Must be provably reciprocal (both users liked each other) — this is business logic, not something a client can be trusted to assert |
| Sending a chat message | The client, directly to Firestore | Once a match exists, message delivery is pure data sync — Firestore's real-time listeners already do exactly what a chat needs, so there's no reason to relay it through a server |

The real-time path stays client SDK → Firestore → other client's listener, enforced by rules instead of API code. On top of that, FastAPI now also exposes a REST surface for messaging ([routers/messages.py](../backend/app/routers/messages.py), [services/messages.py](../backend/app/services/messages.py)) for clients that prefer plain HTTP over a live listener:

- `POST /matches/{matchId}/messages` — send a message (same participant + `status == "active"` checks the rules enforce, done server-side)
- `GET /matches/{matchId}/messages?limit=&before=` — read a thread, newest first, paginated by message ID
- `POST /matches/{matchId}/read` — zero out your own `unreadCount` on the match
- `GET /messages/sent` — every message you've sent, across all matches (collection-group query)

Both write paths land in the same `matches/{matchId}/messages` subcollection, so the `on_message_created` Cloud Function fires identically for either — `lastMessage`, `unreadCount`, and the FCM push all behave the same regardless of how the message was sent.

## Data model

Full field-by-field reference (types, who writes what, what's rule-enforced vs. conventional) is in [DATA_SCHEMA.md](DATA_SCHEMA.md). Summary:

```
matches/{matchId}                          # matchId = sorted "uidA_uidB"
  users: [uidA, uidB]
  status: "active" | "unmatched"
  createdAt: timestamp
  lastMessage: { text, senderId, createdAt } | null
  unreadCount: { [uid]: number }

matches/{matchId}/messages/{messageId}     # auto-generated ID, ordered by createdAt
  senderId: string
  text: string
  imageUrl: string | null                  # not yet implemented, reserved
  createdAt: timestamp                     # set with Firestore SERVER_TIMESTAMP
  readAt: timestamp | null
```

`lastMessage` and `unreadCount` are denormalized onto the `matches` doc so the match list screen (`GET /matches`) can render previews and badge counts with a single document read per match, instead of a subquery into every thread.

## End-to-end flow

1. **Swipe.** Client calls `POST /swipes/{targetUid}` with `{ action: "like" }`. FastAPI runs the transaction in [matching.py](../backend/app/services/matching.py): read the other user's swipe on you first (Firestore requires all transaction reads before any writes), and if it's also a "like," create the `matches/{matchId}` doc.
2. **Match created event.** The Firestore document creation itself is the trigger — no message passing needed. A Cloud Function, `on_match_created` in [functions/main.py](../functions/main.py), fires automatically, looks up both users' FCM tokens, and pushes "You made a new friend!" to both phones.
3. **Chat unlocks.** The client's match-list screen is already listening to `matches` where `users array-contains myUid` (via `GET /matches`, backed by [matches.py](../backend/app/services/matches.py)), so the new match appears without a manual refresh once the client re-polls or re-subscribes.
4. **Opening a thread.** The client attaches a real-time Firestore listener directly to `matches/{matchId}/messages`, ordered by `createdAt`. No backend call — this is a client SDK query protected by [firestore.rules](../firestore.rules).
5. **Sending a message.** The client writes a new document straight into `matches/{matchId}/messages` using the Firestore SDK. The write only succeeds if the security rule passes (see below). Every other client with an open listener on that path receives the new message within the same real-time stream — this is what makes it feel instant without any polling or sockets.
6. **Side effects.** The new message document also fires `on_message_created` in [functions/main.py](../functions/main.py), which: updates `matches/{matchId}.lastMessage`, increments `unreadCount` for the recipient, and sends an FCM push if the recipient isn't actively looking at the thread.
7. **Unmatching.** `POST /matches/{matchId}/unmatch` (FastAPI) flips `status` to `"unmatched"`. The message-create rule requires `status == "active"`, so no new messages can be written from that moment; history stays readable by both parties (a deliberate choice — it preserves evidence for reports). A later re-like by either side does **not** resurrect the match: the swipe transaction sees the existing unmatched doc and reports no match.

## Why the security rules are shaped this way

From [firestore.rules](../firestore.rules):

```
match /matches/{matchId} {
  allow read: if isSignedIn() && request.auth.uid in resource.data.users;
  allow write: if false;   // only the backend (Admin SDK) creates/updates matches

  match /messages/{messageId} {
    allow read: if isSignedIn()
                && request.auth.uid in get(/databases/$(database)/documents/matches/$(matchId)).data.users;
    allow create: if isSignedIn()
                  && request.auth.uid in get(/databases/$(database)/documents/matches/$(matchId)).data.users
                  && get(/databases/$(database)/documents/matches/$(matchId)).data.status == "active"
                  && request.resource.data.senderId == request.auth.uid;
    allow update, delete: if false;
  }
}
```

Four things this guarantees without any server involvement:

- **You can't read or write into a chat you're not part of.** Every read/create checks membership in the parent match's `users` array via `get()`, so even if a client knew a `matchId`, it can't fetch messages for someone else's conversation.
- **You can't impersonate the other person.** `senderId` in the message being created must equal the caller's own authenticated uid — a client can send a message *as* itself, never *as* the other party.
- **Messages are immutable once sent.** `allow update, delete: if false` — no editing or deleting after the fact. If "delete for me" or "edit message" becomes a requirement later, that needs a deliberate rule change (e.g. a soft-delete flag scoped per-user rather than a real delete).
- **Messaging stops when the match ends.** The create rule re-reads the parent match's `status` and only allows writes while it's `"active"`, so an unmatch takes effect immediately at the rules layer — the other party can't keep sending through the client SDK.

## Client responsibilities (not yet built — this repo is backend-only)

Whatever client you build (the Dart/Flutter tooling suggests Flutter) needs to:

- Sign in with Firebase Auth and keep the client SDK's Firestore instance authenticated — the ID token is what the security rules check against, not an API key.
- Subscribe to `matches` (`array-contains myUid`, `status == "active"`) for the match list, and to `matches/{matchId}/messages` (ordered by `createdAt`, paginated with `limit()` + `startAfter()` cursors for scroll-back) for an open thread.
- Write new messages directly via the Firestore SDK (preferred for real-time UX), or through `POST /matches/{matchId}/messages`.
- Call `POST /matches/{matchId}/read` when the thread is opened to zero out `unreadCount.{myUid}`.
- Register an FCM token on sign-in/app-open via `POST /profile/me/fcm-tokens` (and remove it on sign-out with `DELETE /profile/me/fcm-tokens?token=...`) so the notification Cloud Functions have devices to fan out to.

## Known gaps / deliberately out of scope for MVP

- **Per-message read receipts aren't wired up.** Thread-level unread counts are handled (`POST /matches/{matchId}/read`), but `messages.readAt` remains reserved — no rule permits a client to `update` a message doc, and no endpoint writes it. To support it, add a narrow rule allowing the *recipient* (not the sender) to update only the `readAt` field.
- **No typing indicators / presence.** Would be a small addition — a client-writable `matches/{matchId}/typing/{uid}` doc with a rule scoped the same way as messages, ignored by both backend and Cloud Functions.
- **No media messages.** `imageUrl` is reserved in the schema but nothing uploads or renders it yet; would reuse the same Storage-direct-upload pattern used for profile photos, scoped to a `matches/{matchId}/media/` path with rules mirroring the message rules.
- **No spam/rate limiting on message sends.** Since clients write directly to Firestore, there's no FastAPI request to rate-limit. If abuse becomes a problem, options are: Firestore rules with a request-count check (hard to do well), an App Check requirement, or moving message *creation* behind a Cloud Function callable instead of a direct client write (trades away some simplicity for control).
