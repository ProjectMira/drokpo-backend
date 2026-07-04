# Deploying

Pushing to `main` runs [.github/workflows/deploy-backend.yml](../.github/workflows/deploy-backend.yml), which:

1. **Deploys the FastAPI app to Cloud Run** — `gcloud run deploy --source backend`, which builds [backend/Dockerfile](../backend/Dockerfile) via Cloud Build and ships it as the `changsa-api` service in `us-central1`.
2. **Deploys Firebase config** — Firebase Hosting (the `/api/**` rewrite to that Cloud Run service), Firestore rules/indexes, Storage rules, and the Cloud Functions in `functions/`.

The two are separate jobs with the second depending on the first (`needs: deploy-cloud-run`), because Firebase Hosting validates that the Cloud Run service referenced by a rewrite already exists — on a first-ever deploy, deploying Firebase config before the Cloud Run service exists would fail. The workflow only triggers on changes under `backend/**`, `functions/**`, or the Firebase config files — an unrelated doc change won't trigger a deploy. `workflow_dispatch` is enabled too, for a manual re-run.

## One-time setup

### 1. Create a deploy service account

In the Google Cloud project backing your Firebase project:

```bash
gcloud iam service-accounts create changsa-deployer \
  --display-name="Changsa CI deployer"

PROJECT_ID=your-project-id
SA_EMAIL=changsa-deployer@${PROJECT_ID}.iam.gserviceaccount.com

for role in \
  roles/run.admin \
  roles/iam.serviceAccountUser \
  roles/cloudbuild.builds.editor \
  roles/artifactregistry.writer \
  roles/firebase.admin \
  roles/cloudfunctions.developer \
  roles/storage.admin; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="$role"
done
```

`roles/storage.admin` is broader than ideal — Cloud Build needs write access to the GCS bucket it stages source uploads into, and scoping that to just the one bucket is more setup than this MVP needs. Tighten it later if this becomes a real security boundary.

This uses a downloadable JSON key (`credentials_json` in the workflow) rather than Workload Identity Federation, because it's the simpler path to get CI running. **WIF is the more secure option for a production setup** — no long-lived key to leak or rotate — and worth migrating to once this is past the prototype stage; see [google-github-actions/auth](https://github.com/google-github-actions/auth#setting-up-workload-identity-federation).

### 2. Enable the required APIs

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  firebase.googleapis.com \
  firestore.googleapis.com \
  firebasestorage.googleapis.com \
  cloudfunctions.googleapis.com \
  --project "$PROJECT_ID"
```

### 3. Generate the key and add GitHub secrets

```bash
gcloud iam service-accounts keys create changsa-deployer-key.json \
  --iam-account="$SA_EMAIL"
```

In the repo's GitHub Settings → Secrets and variables → Actions, add:

| Secret | Value |
|---|---|
| `GCP_SA_KEY` | The full contents of `changsa-deployer-key.json` |
| `GCP_PROJECT_ID` | Your Firebase/GCP project ID (e.g. `changsa-prod`) |
| `STORAGE_BUCKET` | Your default Storage bucket name — check Firebase Console → Storage rather than assuming a pattern; bucket naming changed from `<project-id>.appspot.com` to `<project-id>.firebasestorage.app` for projects created after October 2024 |

Delete `changsa-deployer-key.json` locally after pasting it in — it's a live credential.

### 4. First deploy

Push to `main` (or run the workflow manually via **Actions → Deploy backend → Run workflow**). After it succeeds once, the Cloud Run service and Firebase Hosting rewrite both exist, and subsequent deploys update them in place.
