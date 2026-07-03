# Changsa API

FastAPI backend for the Changsa Tibetan marriage/dating app, deployed to Cloud Run and fronted by Firebase Hosting.

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

## Photo upload flow

Photos are uploaded directly from the client to Firebase Storage using the Firebase SDK (not through this API), to path `users/{uid}/photos/{photoId}.jpg`. `storage.rules` restricts writes to the owning uid. After upload, the client calls `POST /api/onboarding/photos/confirm` (or `POST /api/profile/me/photos`) with the `storagePath` so the backend can verify the blob exists and record it on the user's profile.

## Deploying

Pushing to `main` deploys automatically via [.github/workflows/deploy-backend.yml](../.github/workflows/deploy-backend.yml) — see [docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md) for the one-time GCP/GitHub secrets setup it needs.

To deploy by hand instead:

```bash
# Build and deploy the API container to Cloud Run
gcloud run deploy changsa-api --source backend --region us-central1 --allow-unauthenticated

# Deploy Hosting rewrite, Firestore rules/indexes, Storage rules, and Cloud Functions
firebase deploy --only hosting,firestore,storage,functions
```

The Cloud Functions in `functions/` handle FCM push notifications on new match/message Firestore events — they are separate from this FastAPI service and deploy independently.
