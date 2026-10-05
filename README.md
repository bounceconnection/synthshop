# SynthShop

A single-owner, locally hosted photo-to-Reverb listing assistant. Launch the website from the CLI, upload actual item photos, review identity and owner facts, research comparable prices, edit the draft, and explicitly approve the exact listing for **bounceconnection**.

Nothing is written to Reverb during upload, identification, editing, research, or final review. Publication has one durable attempt per draft; an uncertain response is not retried as a new listing.

## Install and launch

Python 3.11+ on macOS or Linux:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
chmod 600 .env
synthshop serve
```

The launcher prints a one-time unlock link (`http://127.0.0.1:8765/unlock?key=…`) and opens it in the browser. Use `synthshop serve --no-open --port 8766` to choose another port and open the printed link yourself. It binds **only to 127.0.0.1**; there is deliberately no LAN/public-host option. Stop with Ctrl-C.

Redeeming the link sets a browser session cookie; it works once per launch. Requests without that session, including other local accounts that can reach the port, get no drafts, photos, review or publish actions. Wrong or already-used links are refused. Restart `synthshop serve` for a new link, for example after clearing cookies or switching browsers. This does not protect against software running as your own OS user.

Managed storage defaults to `~/.synthshop`. Use `--data-dir /absolute/private/dedicated-directory` or `DATA_DIR` to change it. Do not point it at a shared/general-purpose directory: SynthShop restricts that directory to the current OS user. The old `identify`, `publish --live`, `sold`, and `unpublish` commands were removed; there is no CLI approval bypass or separate publishing workflow. Manage existing listing endings/sales on Reverb itself.

## Workflow

1. **Photos.** Upload one or more current-item photos: front, rear, readable labels, accessories, and flaws. JPEG, PNG, WebP, and still GIF are decoded by content, not filename. Limits: 25 photos per draft, 20 MiB per file, 40 megapixels, 80 MiB per upload batch. Corrupt/unsupported/animated input is rejected. Private originals are retained; EXIF orientation is corrected, and metadata-free JPEG derivatives (up to 1600 pixels per side) are used for preview, vision, and eventual publication. Reorder the gallery; the first image is the cover. Review serial labels and backgrounds yourself.
2. **Identity and facts.** Configure OpenAI to request a real structured vision candidate, observations, confidence and targeted questions. The model also proposes concise copy for review. Photos do **not** prove function, testing, revision, rarity, smoke-free history or accessories. Confirm/correct maker, model, variant, condition, testing, cosmetics, faults, modifications and inclusions. Unknown facts remain unknown: untested units carry no testing claim, and the draft asks how the unit was tested. Custom panels are not attributed to a guessed maker.
3. **Copy.** Edit the saved title/description. Concise maker/model and verified package distinctions, short unit-specific prose, explicit flaws and an Included list fit the intended style. Analysis fills the editable title and description with the model's proposed copy, which is instructed to use only your facts; verify every claim before saving. Without an API key, saving fills an empty title/description from your facts. Saving nonempty fields accepts them as owner text. Regeneration cannot overwrite accepted facts/copy. Save before other actions; facts do not silently rewrite existing owner copy.
4. **Evidence.** Research Reverb sold and active listings automatically, then inspect exact model, edition, package, condition and duplicate/relist history. Include/exclude observations with a rationale. Add manual evidence when a marketplace does not expose permitted data. Research and price reasoning stay separate from public copy.
5. **Price and shipping.** Until you change them, the price and private reasoning follow the current supported recommendation, and clear when the reviewed evidence no longer supports one; then enter an explicit USD ask and reason. A price or reasoning you change is yours and is never replaced; blank it to return to the recommendation. Legacy-imported prices count as yours. Offers are reviewable. Continental-US shipping is explicitly free (`US_CON`, verified as “Continental U.S.” against Reverb reference data). Local pickup is off. No international destination is enabled automatically. Additional rates use one `CODE=USD amount` per line, such as `CA=50.00`. Load current destination definitions before choosing. `XX` means Everywhere Else. Around $50 for modules and $150–250 for rack gear are planning guidance, not tariffs; packed size/weight, destination and actual cost matter. A desktop meter is not automatically a module or rack item.
6. **Review and approve.** Load current Reverb category/condition choices; do not substitute a category slug for its UUID. Final review shows the exact title, description, condition/category identifiers, ordered photo identities/hashes, price/currency, shipping destinations/rates, offers, inventory and target environment/shop. Publication requires a checked explicit approval and the review token. Any edit, regeneration, reorder, or change to the Reverb token, API environment, expected shop or R2 staging settings invalidates that review. Vision and local-storage settings do not. Stale tabs are rejected.
7. **Publish or reconcile.** The backend verifies the authenticated account/shop, stages only approved derivatives, creates a remote **draft**, immediately persists its ID, verifies ownership/content/photo ingestion, then updates it with `publish=true`. Success requires a read-back live state, matching fields/photo order, authenticated own-listing membership and a Reverb URL. Repeated clicks/restarts use the same attempt and stable `synthshop-<draft-id>` SKU. No automatic delete, end, relist or second create occurs after an ambiguous response.

Manual identification and owner pricing use the same final approval path. No supplied test photograph is publication approval.

## Evidence and pricing semantics

The anonymous Reverb `/listings?show_only_sold=true` path currently returns sold-tagged **displayed listing prices**, not proven accepted offers or paid amounts. SynthShop validates returned state and keeps these separate from active asking prices. This filter's long-term support, ordering and retention are not guaranteed; a publication timestamp is never a sale date. Each research action is bounded to 40 sold and 40 active results, not a complete market census.

Observations retain source URL/ID/provider, source class, provenance, capture time, known publication/sale dates, condition/package information, amount, display/original currency, shipping/tax treatment, match rationale and exclusions. Strict structured maker/model checks flag obvious mismatches; owner review is still required for variants, clones, bundles, accessories and relists. Duplicate source IDs and explicitly linked mirrors are excluded. Changing identity or condition makes research stale. Refreshing research replaces earlier automatic observations; manual observations are kept.

Recommendations require at least two reviewed comparable sold observations in one evidence class with USD **original and displayed currency**, known continental-US shipping, and no tax-inclusive amount requiring normalization. Confirmed transactions are preferred; otherwise sold-page displayed prices are used with their limitations. The ask is the decimal median of item + stated shipping, reflecting free domestic shipping. Below five observations the full observed range is labeled a weak/small sample; otherwise the central half is shown. No invented condition/accessory adjustment, sale-date FX conversion or date confidence is added. Active asks, owner reports, guide aggregates, unknown shipping and unsupported currencies remain visible context, not silently equivalent transactions. Additional taxes/fees are not normalized.

Without sufficient evidence, the UI says so and the price stays blank for an explicit owner price/reason. An owner's price recollection and a sold page's displayed asking price remain separate observations; neither automatically proves the amount paid.

### Manual entry/import

Use the expandable manual-evidence form to paste a JSON array of 1–100 factual observations (maximum 200 KB). URLs are citations and are not fetched. Do not paste buyer names, addresses, payment information, credentials or private order records.

```json
[{
  "provider": "eBay",
  "url": "https://www.ebay.com/itm/EXAMPLE",
  "source_id": "EXAMPLE",
  "source_class": "sold_display",
  "provenance": "Owner observed sold page; accepted offer unknown",
  "title": "Exact maker/model/variant and package",
  "condition": "Very Good",
  "amount": "100.00",
  "currency": "USD",
  "listing_currency": "USD",
  "shipping": null,
  "shipping_region": "unknown",
  "match_rationale": "Explain exact edition, condition and package"
}]
```

Classes: `sold_display`, `active_ask`, `confirmed_transaction`, `owner_report`, `guide_aggregate`. Only select confirmed transaction when your permitted source establishes that actual amount. Add `sale_date` only if known; use `published_at` separately. `mirror_of` records duplicate/mirrored observations. Unknown shipping is `null`, not zero. Imported records require match review; raw imported claims are not independently authenticated.

There is no ModularGrid HTML/DDG/brute-force scraping path. ModularGrid/eBay/other permitted owner observations can be imported. Restricted Price Guide or logged-in marketplace access is not bypassed; an aggregate is not expanded into invented sales.

## Backend configuration

Keys belong only in your protected backend `.env` or process environment. They never appear in browser settings/storage, draft JSON, model context or rendered errors. Configuration readiness displays presence, **not** authenticated access or token scopes.

- `OPENAI_API_KEY`: required for vision, not for manual drafting/research. `VISION_MODEL` defaults to `gpt-4.1-mini`; your OpenAI API account must have access and quota. This documented [image-input, Structured Outputs model](https://developers.openai.com/api/docs/models/gpt-4.1-mini) uses the [Responses API with strict JSON Schema](https://developers.openai.com/api/docs/guides/structured-outputs). Overrides should be non-reasoning models that support both image input and Structured Outputs. Each request has a fixed 2,200 output-token budget. Reasoning models (such as the gpt-5 or o-series families) spend hidden reasoning tokens from that budget; when it runs out the response is incomplete, Analyze reports that vision did not complete, and the saved draft is unchanged. There is no alternate-provider fallback. Upgrading an existing installation: set `OPENAI_API_KEY` and replace or remove any old `VISION_MODEL=claude-…` pin in `.env`, because a Claude model ID makes every analysis fail. `ANTHROPIC_API_KEY` is no longer read.
- `REVERB_API_TOKEN`: a personal token for the intended shop. Publication needs `public`, `read_profile`, `read_listings`, and **`write_listings`**. A read-only history token cannot publish. No order scopes or account provisioning are needed by this workflow.
- `REVERB_BASE_URL`: production `https://api.reverb.com/api`, or separately authorized sandbox `https://sandbox.reverb.com/api`. Other hosts are rejected. Sandbox requires a separate account/token; production tokens are not assumed to work there.
- `EXPECTED_SHOP_ID`, `EXPECTED_SHOP_SLUG`: default `1333667`, `bounceconnection`. Production publishing is restricted to that identity. Sandbox settings may name a separately authorized test shop. Public identity alone is not token ownership proof.
- `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`: private image staging. Use an already provisioned private bucket with object read/write/delete and lifecycle-configuration read access. No public bucket URL/ACL is used. Before uploading, the app verifies an existing enabled **one-day expiry** covering the `synthshop/` prefix; missing/inaccessible expiry blocks staging. This bounds leftovers after crashes/failed attempts. The app does not provision or change bucket policies. Approved JPEGs use random photo keys and one-hour signed HTTPS GET URLs sent only to Reverb. Successful verified publication deletes known staged objects; reconciliation retries cleanup if needed. Failed/ambiguous attempts retain staging until lifecycle expiry, never indefinite public exposure.
- `DATA_DIR`: dedicated managed local storage; defaults to `~/.synthshop`.
- `PRODUCTS_DIR`: legacy JSON import source; defaults to `products` in the launch directory.

Analysis sends only the current item's orientation-corrected, metadata-free JPEG derivatives and relevant identity/owner facts (maker, model, variant, testing, cosmetics, faults, modifications and inclusions; saved condition stays local) to `https://api.openai.com/v1/responses`. Originals, local paths, credentials, inventory and private price research are not model input. Requests set `store=false`; this is not a promise of zero provider retention—OpenAI's account/data policies still apply. Refusal, incomplete/invalid output, missing credentials, quota and provider errors retain the saved draft and report failure rather than a successful identification. Requests are not automatically retried. Analysis never starts Reverb research, image staging or publication.

Complete seller eligibility, shop/billing/payout details and return policies directly on Reverb. The API does not bypass them. No tunnel, storage purchase, live listing or production image upload is needed to develop this application.

## Durability, migration and recovery

SQLite stores each draft's latest revision and revision counter, approval snapshots, import markers, and one publish-attempt record per draft. Money uses `Decimal`. Photos are addressed by managed random IDs; filenames cannot escape the photo directory. Cross-process publication locks plus transactional revision checks prevent duplicate-click creates.

Existing `PRODUCTS_DIR/*.json` files are imported once into SQLite without changing/deleting the originals. Reverb IDs and legacy lifecycle state are preserved; linked or non-draft records are **blocked from fresh creation**. Legacy photos are not silently trusted or published: re-upload current images for an unlinked draft and review all fields. Import errors are visible and block publication until resolved. Keep a backup of the original directory; do not remove remote IDs to force a create.

After a timeout or restart, reopen the locked draft and review/reconcile the same attempt. The app searches authenticated own listings by the exact stable SKU with `state=all`. An empty or multiple-result lookup leaves the outcome unknown; it never authorizes another POST. A remote ID, once known, is durable even when photos or publication remain unverified. Check the SKU/listing in Reverb and resolve provider/account/media problems before reconciling. Do not manually erase attempt rows, change the target account, or duplicate the item to “retry.” Previously published records are never automatically relisted.

Two outcomes prove no listing exists, so the attempt is released for correction and fresh approval under the same SKU: a failure or interruption before any create is sent (for example during image staging; an interrupted attempt is released once no publish is running), or a create rejected with HTTP 401, 403 or 422 (authentication, permission or validation). Timeouts, 5xx, other 4xx responses and empty lookups remain unknown and keep the draft locked for reconciliation.

Back up the entire managed directory, not just the database. Original photos intentionally remain local; keep the directory private and outside source control. `products/`, local managed directories, `.env` and SQLite files are ignored by Git.

## Provider contracts and verification limits

Official references:

- [Personal tokens](https://www.reverb-api.com/docs/authentication) and [scopes](https://www.reverb-api.com/docs/personal-token-scopes-1)
- [Create listings, conditions/categories, explicit shipping and unique inventory](https://www.reverb-api.com/docs/create-a-listing)
- [Find by SKU, update and publish](https://www.reverb-api.com/docs/updating-your-listing)
- [Image URL ingestion/order](https://www.reverb-api.com/docs/updating-listing-images)
- [Account details](https://www.reverb-api.com/docs/account-details), [sandbox](https://www.reverb-api.com/docs/testing-on-sandbox)
- [R2 S3 operation compatibility, including lifecycle reads](https://developers.cloudflare.com/r2/api/s3/api/)

Reverb ingests accessible HTTPS image URLs. This app does not send localhost/blob URLs or assume a multipart upload endpoint. It uses the documented photo URL array in draft creation, then update/publish and result reads. Unexpected ownership, schemas, missing photos or field differences fail closed rather than pretending success.

Live verification is scoped to tested commit `80b37289eaeda70c229c5e25cfbfcf2a4167ca35`. The real `synthshop serve` launcher and Chrome were run on loopback with isolated local storage and no `.env` or provider credentials. Observed at that commit:

- **Local workflow:** photo intake (content-detected formats, EXIF orientation correction, metadata-free derivatives, non-image and animated-GIF rejection), gallery order, owner facts and copy, fact-based copy without an AI call, the missing-key Analyze refusal, manual evidence match review with recommendation-following price/reasoning and surviving owner overrides, malformed money/shipping rejection without a 500 or save, and exact review with approval disabled and an approval-less publish POST refused before any attempt.
- **One-time unlock and access controls:** an HttpOnly, SameSite=Strict session; wrong or reused unlock links, cookieless requests, a wrong CSRF token and a cross-origin form post were refused.
- **Restart persistence:** after relaunch the old link and session were refused; the new link restored drafts, photo order, copy, evidence, price/reasoning and shipping rates.
- **Anonymous Reverb reads:** current categories and conditions, and sold/active research observations, which set no price until reviewed. This shows reachability, not authenticated compatibility.

That run supplements the recorded Test exception: the automated Test phase could not bind a loopback socket or reach Reverb in its sandbox, so its scenarios ran in-process with transport doubles and are not relabeled as live. Malformed-row and model/bundle/condition mismatch research branches were not injected live. Duplicate-click, ambiguous-create, lost create/publish responses, SKU reconciliation without replay, prepared-attempt crash recovery and ownership/media failure paths are covered only in-process by tests with behavioral provider doubles; **they are not proof of real provider compatibility**.

Authenticated vision inference, authenticated bounceconnection ownership/scopes, Reverb draft/photo ingestion/update/live read-back, and R2 signed-URL compatibility still require separately authorized credentials and a sandbox account/bucket. They were not exercised against a real account during implementation or the live verification above. No production write is authorized by installation or by the development fixture.

## Development

```bash
pytest
ruff check src tests
pylint src/synthshop/
python -m pip install build
python -m build
```

Tests isolate storage and credentials; no live API keys are required. Keep tests focused on owner precedence, money/evidence boundaries, stale approval, persistence, migration, security and uncertain remote outcomes—not provider mock echoes as compatibility evidence.

Core application services live in `core/application.py` and `core/publishing.py`; FastAPI/Jinja routes live in `web/`; Typer only launches the local UI. There is no native app, multi-user hosting, storefront/Stripe checkout, cross-posting, order management or generalized scraping platform.
