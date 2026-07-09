# Drokpo API

FastAPI backend for Drokpo, the friend-making app for Tibetans, deployed to Cloud Run and fronted by Firebase Hosting.

## Local setup

1. Create a Firebase project, enable Authentication (Phone), Firestore, and Storage.
2. Download a service account key for local dev and save it as `backend/service-account.json` (never commit this).
3. Copy `.env.example` to `.env` and fill in your project ID and storage bucket.
4. Install dependencies and run:

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Every request must include `Authorization: Bearer <Firebase ID token>` from a signed-in client.

All routes are mounted under `/api` (e.g. `GET /api/profile/me`, `POST /api/swipes/{uid}`) to match the Firebase Hosting rewrite, which forwards the path to Cloud Run unstripped. `/health` also exists at the root for Cloud Run probes.

## Endpoints

| Area | Endpoints |
|---|---|
| Onboarding | `POST /api/onboarding`, `POST /api/onboarding/photos/confirm`, `POST /api/onboarding/complete` |
| Profile | `GET /api/profile/me`, `PATCH /api/profile/me` (every field is editable, including `dob`, `gender`, `location`, `socials`), photo add/delete, FCM token register/remove |
| Feed | `GET /api/feed` — nearby people ranked by shared interests |
| Swipes | `POST /api/swipes/{uid}`, `GET /api/swipes?action=like` (everything you've sent, filterable) |
| Matches | `GET /api/matches`, `POST /api/matches/{matchId}/unmatch` |
| Messages | `POST /api/matches/{matchId}/messages`, `GET /api/matches/{matchId}/messages` (paginated with `before`), `POST /api/matches/{matchId}/read`, `GET /api/messages/sent` |
| Safety | `POST /api/reports`, `POST`/`DELETE /api/blocks/{uid}` |

Profiles carry a `socials` map (`instagram`, `youtube`, `tiktok`, `facebook`, `x`, `wechat`) — **Instagram is required** at onboarding and can be changed but never cleared; the rest are optional.

## Tests

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest
```

Tests cover every endpoint with the auth dependency overridden and the service layer stubbed, so they run without any Firebase credentials.

## Photo upload flow

Photos are uploaded directly from the client to Firebase Storage using the Firebase SDK (not through this API), to path `users/{uid}/photos/{photoId}.jpg`. `storage.rules` restricts writes to the owning uid. After upload, the client calls `POST /api/onboarding/photos/confirm` (or `POST /api/profile/me/photos`) with the `storagePath` so the backend can verify the blob exists and record it on the user's profile.

## Deploying

Pushing to `main` deploys automatically via [.github/workflows/deploy-backend.yml](../.github/workflows/deploy-backend.yml) — see [docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md) for the one-time GCP/GitHub secrets setup it needs.

To deploy by hand instead:

```bash
# Build and deploy the API container to Cloud Run
gcloud run deploy drokpo-api --source backend --region us-central1 --allow-unauthenticated

# Deploy Hosting rewrite, Firestore rules/indexes, Storage rules, and Cloud Functions
firebase deploy --only hosting,firestore,storage,functions
```

The Cloud Functions in `functions/` handle FCM push notifications on new connection/message Firestore events — they are separate from this FastAPI service and deploy independently.
