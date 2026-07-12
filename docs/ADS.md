# Ads in the Discover deck

Drokpo shows sponsored cards inside the Discover deck: after every **3 real profiles**, the app inserts one ad card. Members swipe it like any other card — swiping **right (like)** opens the ad's link in the in-app browser; swiping left just dismisses it. Nothing is written to their swipe history for ads.

## How to add an ad

1. Open the [Firebase console](https://console.firebase.google.com/project/drokpo-backend/firestore) → Firestore → `ads` collection (create it with the first ad).
2. Add a document (auto-id is fine) with these fields:

   | Field | Type | Required | Example |
   |---|---|---|---|
   | `active` | boolean | ✅ | `true` |
   | `title` | string | ✅ | `"Lhasa Momo House"` |
   | `linkUrl` | string | ✅ | `"https://lhasamomo.example.com"` |
   | `body` | string | – | `"Hand-made momos in Jackson Heights. 10% off for Drokpo members."` |
   | `ctaLabel` | string | – | `"Order now"` (defaults to "Learn more") |
   | `imageUrl` | string | – | public https image URL |
   | `order` | number | – | `1` — lowest serves first; defaults to 0 |

3. For the image you can either paste any public https URL into `imageUrl`, **or** upload the file to Storage under `ads/` (e.g. `ads/momo-house.jpg`) and instead add a `photos` array field with one map entry: `{ storagePath: "ads/momo-house.jpg" }`. The API resolves a download `url` for each `storagePath` entry server-side, and the `optimize_photo` Cloud Function automatically downscales uploads under `ads/` to a phone-sized JPEG — so uploading a large original is fine.
4. That's it — no deploy needed. The next `GET /api/feed` includes the ad (ads are cached in the API for up to 60 seconds, so console edits can take a minute to show).

## Pausing / rotation / results

- Flip `active` to `false` to pull an ad immediately.
- Multiple active ads rotate: the app cycles through them in `order` sequence as the deck refills.
- `impressions` (ad card reached the top of the deck) and `clicks` (link opened) accumulate on the ad document; check them right in the console.
