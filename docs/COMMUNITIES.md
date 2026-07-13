# Community accounts

Drokpo supports a second account type alongside personal profiles: **communities** (organizations, groups, events) that publish posts into the Discover deck instead of swiping. A Firebase Auth account is either a person (`users/{uid}`) or a community (`communities/{uid}`) — never both; the backend enforces this with a 409 on whichever onboarding endpoint runs second.

## Registration → review → approval, end to end

1. **A community registers** via `POST /api/communities/onboarding`. `email` is required (and can be changed later but never cleared, like a person's Instagram handle) — it's where the approval notice below eventually goes.
2. **You get emailed.** The `on_community_created` Cloud Function ([functions/main.py](../functions/main.py)) fires immediately and queues an email to the admin address (**ta3tsering@gmail.com** by default — override with the `DROKPO_ADMIN_EMAIL` env var on the functions deploy) with every field the community submitted, plus a direct link to its document in the Firebase console.
3. **You review and flip `verification`** in the console (steps below) — same manual step as before; nothing about the review process itself changed.
4. **The community gets emailed automatically.** The `on_community_verification_changed` Cloud Function fires on that write and, if `verification` became `"verified"`, queues an email to the community's own `email` telling them their account is open and posting has unlocked. A transition to `"rejected"` sends a neutral "we couldn't approve this" note instead.

Both functions only *queue* a document in the `mail` collection — they don't send anything themselves. Delivery is a separate piece described next.

### One-time setup: the Trigger Email extension

Actually sending the queued emails requires installing Firebase's **Trigger Email** extension once, from the machine/account that manages this project (an agent cannot do this step — it needs interactive SMTP credential entry):

```sh
firebase ext:install firebase/firestore-send-email --project drokpo-backend
```

When prompted:
- **Collection**: `mail` (must match what `functions/main.py` writes to)
- **SMTP connection URI**: an SMTP server + credentials capable of sending as your from-address. The simplest option is a Gmail account with an [app password](https://myaccount.google.com/apppasswords) (`smtps://<address>:<app-password>@smtp.gmail.com:465`); a transactional-email provider (SendGrid, Mailgun, Postmark, ...) works too and is more suitable if volume grows.
- **Default FROM address**: whatever you want registrants and the admin to see as the sender.

Until this extension is installed, both Cloud Functions still run and write to `mail` — those documents just sit there unsent (harmless) rather than erroring. Install the extension whenever you're ready; no redeploy of `functions/main.py` is needed for it to start working.

## Verification

Every community starts with `verification: "pending"` after `POST /api/communities/onboarding`. While pending:

- The community can sign in, edit every field (`PATCH /api/communities/me`), and upload/reorder/delete photos — nothing about profile completion is gated.
- Posting is blocked: `POST /api/communities/me/posts` returns `403` with detail `"Posting unlocks once your community is verified"`.
- The community does not appear in `GET /api/communities` (the discover directory) or in the Discover feed's community-post queue.

**To verify a community:**

1. Open the [Firebase console](https://console.firebase.google.com/project/drokpo-backend/firestore) → Firestore → `communities` collection (the admin-notification email links straight to the right document).
2. Find the community's document (its ID is the community's Firebase Auth uid — cross-reference by `name` or `email`).
3. Set `verification` to `"verified"`.

That's it — no deploy needed. The community's next `GET /api/account` / `GET /api/communities/me` call reflects the new status, its posts start showing up in `GET /api/communities/{cid}/posts` and the Discover feed's community-post queue (up to 60s cache), it appears in the directory ordered by `memberCount`, and (once the Trigger Email extension is installed) it receives the approval email described above.

Set `verification` to `"rejected"` to permanently exclude a community without deleting its data — same effect as `"pending"` on posting/visibility, but signals the review is final rather than in-progress, and sends the community a rejection notice instead of an approval one.

## Roles / access control

An account's role is never stored as a field — it's derived from which document exists for a given uid, and onboarding guarantees at most one of `users/{uid}` / `communities/{uid}` exists. `app/dependencies.py`'s `require_person_uid` / `require_community_uid` resolve this once per request and gate the relevant endpoints (403 with a clear detail message on a mismatch): swipes, matches, messages, the profile endpoints, `GET /feed`, join/leave, voting, and RSVPing are person-only; everything under `/communities/me` (profile, photos, posts) is community-only. Read-only community endpoints (the directory, a community's detail page, its public post list) accept either role. Member lists (`GET /api/communities/{cid}/members`) are a special case — gated by membership itself (`is_member_or_self`), not account type, since either a person who joined or the community itself may view them.

## Fields

See [DATA_SCHEMA.md](DATA_SCHEMA.md) for the full `communities/{uid}` and `communityPosts/{postId}` field tables.
