# SynthShop

A single-owner, locally hosted photo-to-Reverb listing assistant. Launch the website from the CLI, upload actual item photos, review identity and owner facts, research comparable prices, edit the draft, authorize one unpublished remote draft, then inspect its processed gallery and independent cover before separately approving publication for **bounceconnection**.

Upload, identification, editing, research and review screens make no Reverb or staging writes. Preparation and publication have distinct purpose-bound approvals. One durable attempt/SKU retains at most one create opportunity and one publish intent; uncertain writes are not replayed.

## Install and launch

Python 3.11+ on macOS or Linux:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
synthshop setup-openai  # optional: hidden terminal prompt for photo analysis
synthshop serve
```

The launcher prints a one-time unlock link (`http://127.0.0.1:8765/unlock?key=…`) and opens it in the browser. Use `synthshop serve --no-open --port 8766` to choose another port and open the printed link yourself. It binds **only to 127.0.0.1**; there is deliberately no LAN/public-host option. Stop with Ctrl-C.

Redeeming the link sets a browser session cookie; it works once per launch. Requests without that session, including other local accounts that can reach the port, get no drafts, photos, review or publish actions. Wrong or already-used links are refused. Restart `synthshop serve` for a new link, for example after clearing cookies or switching browsers. This does not protect against software running as your own OS user.

Managed storage defaults to `~/.synthshop`. Use `--data-dir /absolute/private/dedicated-directory` or `DATA_DIR` to change it. Do not point it at a shared/general-purpose directory: SynthShop restricts that directory to the current OS user. The old `identify`, `publish --live`, `sold`, and `unpublish` commands were removed; there is no CLI approval bypass or separate publishing workflow. Manage existing listing endings/sales on Reverb itself.

### Set up OpenAI photo analysis

Click **Set up OpenAI** in Backend readiness or beside the draft's analysis action for
the in-app guide. No credential is collected in the browser.

1. Save draft edits and stop your normal app with Ctrl-C in its launch terminal.
2. Get a key from [OpenAI API keys](https://platform.openai.com/api-keys).
   API billing is separate from a ChatGPT subscription.
3. In that same directory and activated Python environment, run `synthshop setup-openai`.
   Do **not** append your key to the command. Confirm the displayed `.env` destination,
   then paste only at the hidden terminal prompt and press Enter; no characters echo.
4. Restart `synthshop serve` from the same directory with your usual port/storage options.
   Open the launcher's **new unlock link**. Vision should show **Configured**.

Setup makes no API request and does not test authentication, credit or model access.
Only choosing Analyze sends photos to OpenAI and can incur charges. Manual editing
remains available without a key.

The command creates or updates only `OPENAI_API_KEY` in the launch directory's `.env`,
atomically and with owner-only permissions (`0600`). Other entries remain intact.
Replacing an existing key requires confirmation. Empty secret input, declining a
confirmation, or Ctrl-C leaves the original file unchanged. Malformed/non-UTF-8
dotenv files and linked files are refused rather than rewritten; write failures
leave the original configuration intact.

The launch directory—not `--data-dir`—determines which `.env` is read. Environment
variables take precedence: setup refuses to save if `OPENAI_API_KEY` is already
set in the terminal environment, even when empty. Remove that override from the
normal launch terminal before setup. Restart after saving; refreshing a running
app does not reload settings. Keep `.env` out of source control; never put a key
in chat, command arguments, shell history, screenshots or browser forms.

**Offline/test launchers are separate.** A publishing-validation fixture may use
a fresh HOME/environment, fake Reverb/R2 clients and intentionally no vision key,
or ignore `.env`. Do not add real credentials or enable real providers there.
Use the guide in your normal app environment; fixture badges do not prove real
account readiness.


## Workflow

1. **Photos.** Upload one or more current-item photos: front, rear, readable labels, accessories, and flaws. JPEG, PNG, WebP, and still GIF are decoded by content, not filename. A phone JPEG that carries an HDR gain map (Apple or Adobe/Ultra HDR, as one primary photo plus one gain-map picture) is accepted: the derivative is a standard SDR JPEG of the primary photo, and the retained original keeps the gain map. Other multi-picture JPEGs (stereo pairs, panoramas, extra pictures) are rejected, and HEIC/HEIF and RAW are not supported. Limits: 25 photos per draft, 20 MiB per file, 40 megapixels, 80 MiB per upload batch. Corrupt/unsupported/animated input is rejected. Private originals are retained; EXIF orientation is corrected, and metadata-free JPEG derivatives (up to 1600 pixels per side) are used for preview, vision, and preparation uploads. Reorder the gallery; the first image is the intended cover, which must later be checked against Reverb's independent processed cover. Review serial labels and backgrounds yourself.
2. **Identity and facts.** Configure OpenAI to request a real structured vision candidate, observations, confidence and targeted questions. The model also proposes concise copy for review. Photos do **not** prove function, testing, revision, rarity, smoke-free history or accessories. Confirm/correct maker, model, variant, condition, testing, cosmetics, faults, modifications and inclusions. Unknown facts remain unknown: untested units carry no testing claim, and the draft asks how the unit was tested. Custom panels are not attributed to a guessed maker.
3. **Copy.** Edit the saved title/description. Concise maker/model and verified package distinctions, short unit-specific prose, explicit flaws and an Included list fit the intended style. Analysis fills the editable title and description with the model's proposed copy, which is instructed to use only your facts; verify every claim before saving. Without an API key, saving fills an empty title/description from your facts. Saving nonempty fields accepts them as owner text. Regeneration cannot overwrite accepted facts/copy. Save before analysis, research, photo changes or loading references; facts do not silently rewrite existing owner copy. Review includes its own explicit save.
4. **Evidence.** Research Reverb sold and active listings automatically, then inspect exact model, edition, package, condition and duplicate/relist history. Include/exclude observations with a rationale. Add manual evidence when a marketplace does not expose permitted data. Research and price reasoning stay separate from public copy.
5. **Price and shipping.** Until you change them, the price and private reasoning follow the current supported recommendation, and clear when the reviewed evidence no longer supports one; then enter an explicit USD ask and reason. A price or reasoning you change is yours and is never replaced; blank it to return to the recommendation. Legacy-imported prices count as yours. Offers are reviewable. Continental-US shipping is explicitly free (`US_CON`, verified as “Continental U.S.” against Reverb reference data). Local pickup is off. No international destination is enabled automatically. Additional rates use one `CODE=USD amount` per line, such as `CA=50.00`. Load current destination definitions before choosing. `XX` means Everywhere Else. Around $50 for modules and $150–250 for rack gear are planning guidance, not tariffs; packed size/weight, destination and actual cost matter. A desktop meter is not automatically a module or rack item.
6. **Save and review preparation.** Load current Reverb category/condition choices; do not substitute a category slug for its UUID. Choose **Save and review preparation** to check the values currently entered in the editor, save them through the revision check, and open review. A newly typed Maker or Exact model does not need a separate save first. Missing or invalid fields keep you in the editor with your entries retained, a linked summary of all applicable field errors and an error beside each affected field; focus moves to the first invalid field. Nothing is saved on a field-check failure. **Save owner corrections & copy** still allows incomplete drafts, but rejects malformed amounts, shipping rates or other invalid typed input in the same editor. Review shows the exact saved fields, local photo order and intended first-image cover, positive USD ask, shipping and authenticated target shop/environment. Saving or reviewing never stages photos or writes to Reverb. The unchecked preparation authorization permits uploads and **one unpublished remote draft**, not a publish PUT. Local edits, reordered photos and backend credential/destination changes invalidate the challenge. Stale tabs, session/CSRF failures, provider failures and uncertain publication results remain distinct errors, not editable field errors.
7. **Prepare, then stop.** The backend verifies the target, stages approved JPEG derivatives and persists create intent before one `publish=false` POST. It saves the remote ID and captures returned processed images, stopping at `review_ready`. Missing or ambiguous evidence leaves the remote draft locked with no final approval. Use **Check status / refresh evidence** for a bounded user-triggered read; there is no poller or automatic restaging. Production preparation is disabled by default.
8. **Inspect processed gallery and independent cover.** The review page shows every returned gallery entry in order beside its intended local counterpart, a separately visible cover, capture time, remote ID, revision, shop/environment and exact fields. Full-view links serve the same private cached encoded bytes, not new browser downloads from Reverb. Inspect each view, order and cover yourself. Transformed images can be accepted; counts, hashes and similarity do not make that judgment. Declining or leaving retains the remote draft, SKU and local edit lock; correction is manual.
9. **Approve publication once.** A separate unchecked final authorization binds that immutable snapshot. Fresh provider reads must still match its identity, relation, locator digest, entity bytes, media metadata, order, independent cover and approved fields. A changed or unavailable representation invalidates the token and requires a new capture and explicit review. After matching preflight, approval and publish intent are persisted atomically before at most one PUT. Only a matching live readback with verified ownership and a safe Reverb URL earns a verified-at-time result.

Manual identification and owner pricing use the same two-stage path. No supplied test photograph, preparation checkbox, page refresh or status action authorizes publication. Reverb can serve other renditions to buyers, and a change can become public before post-publication detection. There is no atomic compare-and-publish guarantee, automatic rollback, unpublish, delete or relist.

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

To add Reverb or R2 settings, start from the template only when no `.env` exists yet, then keep it owner-only:

```bash
[ -e .env ] || cp .env.example .env
chmod 600 .env
```

Edit an existing `.env` in place instead. Never copy `.env.example` over it, because that erases a key saved by `synthshop setup-openai`. Running `synthshop setup-openai` after copying the template replaces its blank `OPENAI_API_KEY=` entry.

- `OPENAI_API_KEY`: required for vision, not for manual drafting/research. `VISION_MODEL` defaults to `gpt-4.1-mini`; your OpenAI API account must have access and quota. This documented [image-input, Structured Outputs model](https://developers.openai.com/api/docs/models/gpt-4.1-mini) uses the [Responses API with strict JSON Schema](https://developers.openai.com/api/docs/guides/structured-outputs). Overrides should be non-reasoning models that support both image input and Structured Outputs. Each request has a fixed 2,200 output-token budget. Reasoning models (such as the gpt-5 or o-series families) spend hidden reasoning tokens from that budget; when it runs out the response is incomplete, Analyze reports that vision did not complete, and the saved draft is unchanged. There is no alternate-provider fallback. Upgrading an existing installation: set `OPENAI_API_KEY` and replace or remove any old `VISION_MODEL=claude-…` pin in `.env`, because a Claude model ID makes every analysis fail. `ANTHROPIC_API_KEY` is no longer read.
- `REVERB_API_TOKEN`: a personal token for the intended shop. Publication needs `public`, `read_profile`, `read_listings`, and **`write_listings`**. A read-only history token cannot publish. No order scopes or account provisioning are needed by this workflow.
- `REVERB_BASE_URL`: production `https://api.reverb.com/api`, or separately authorized sandbox `https://sandbox.reverb.com/api`. Other hosts are rejected. Sandbox requires a separate account/token; production tokens are not assumed to work there.
- `REVERB_PROCESSED_PHOTO_REVIEW_CONFIRMED`: defaults to `false`, blocking both new production staging/create and the first publish PUT. Do not enable it without separately authorized provider qualification and permission to enable this workflow. It is an operator readiness assertion, not evidence or either seller approval. Read-only status works with it false; sandbox writes still require the relevant explicit seller grants and separate operational authorization. The obsolete `REVERB_EXACT_PHOTOS_CONFIRMED` is ignored, even if true; there is no compatibility alias.
- `EXPECTED_SHOP_ID`, `EXPECTED_SHOP_SLUG`: default `1333667`, `bounceconnection`. Production publishing is restricted to that identity. Sandbox settings may name a separately authorized test shop. Public identity alone is not token ownership proof.
- `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`: private image staging. Use an already provisioned private bucket with object read/write/delete and lifecycle-configuration read access. No public bucket URL/ACL is used. Before uploading, the app verifies an existing enabled **one-day expiry** covering the `synthshop/` prefix; missing/inaccessible expiry blocks staging. It does not provision or change bucket policies. Preparation authorizes uploading approved JPEGs via one-hour signed HTTPS GET URLs sent only to Reverb and deleting only this attempt's known staging keys after verified live success. Cleanup failure is reported separately and can be retried by status without replaying publication. Decline/abandon causes no cleanup; leftovers follow existing expiry. This does not promise privacy or expiry of Reverb-hosted media.
- `DATA_DIR`: dedicated managed local storage; defaults to `~/.synthshop`.
- `PRODUCTS_DIR`: legacy JSON import source; defaults to `products` in the launch directory.

Analysis sends only the current item's orientation-corrected, metadata-free JPEG derivatives and relevant identity/owner facts (maker, model, variant, testing, cosmetics, faults, modifications and inclusions; saved condition stays local) to `https://api.openai.com/v1/responses`. Originals, local paths, credentials, inventory and private price research are not model input. Requests set `store=false`; this is not a promise of zero provider retention—OpenAI's account/data policies still apply. Refusal, incomplete/invalid output, missing credentials, quota and provider errors retain the saved draft and report failure rather than a successful identification. Requests are not automatically retried. Analysis never starts Reverb research, image staging or publication.

Complete seller eligibility, shop/billing/payout details and return policies directly on Reverb. The API does not bypass them. No tunnel, storage purchase, live listing or production image upload is needed to develop this application.

## Durability, migration and recovery

SQLite stores revisioned drafts, purpose-bound review challenges, approved payload/target/local-photo bindings, immutable processed manifests, final grants and publish intent. Money uses `Decimal`. Managed original/derivative photos and encoded processed snapshot blobs use validated generated local IDs. Cross-process workflow locks and transactional claims prevent duplicate creates and consume final approval before the sole PUT. Replaced unapproved captures are removed only after durable replacement; the approved capture remains for audit.

Existing `PRODUCTS_DIR/*.json` files are imported once into SQLite without changing/deleting the originals. Reverb IDs and legacy lifecycle state are preserved; linked or non-draft records are **blocked from fresh creation**. Legacy photos are not silently trusted or published: re-upload current images for an unlinked draft and review all fields. Import errors are visible and block publication until resolved. Keep a backup of the original directory; do not remove remote IDs to force a create.

After a timeout or restart, reopen the locked draft and use **Check status / refresh evidence**, not another approval. The app searches authenticated own listings by exact stable SKU with `state=all` when the ID is unknown. Empty or multiple matches never authorize another POST. After publish intent, status is strictly Reverb-read-only, even if readback says draft: a crash before sending the PUT is indistinguishable from a sent request with a lost response. This can strand an unsent intent deliberately. Do not erase attempts, change targets or duplicate the item to “retry.”

After intent, every verification compares against the **original prepublication approved** processed snapshot. Changed/unreadable evidence clears the verified URL and reports `published_unverified`, distinguishing last-observed-live from unknown outcomes. It may be publicly visible; failure is not rollback. A later matching live read may confirm the original approval without another write or replacing its evidence. Unexpected live state with no final grant cannot acquire approval retroactively. Cached previews survive restart and staging expiry, but never replace fresh provider evidence before publication.

**One-time processed-review cutover:** all old review tokens are invalidated. Original attempt bodies and historical review payloads are retained in `publication_history`; old IDs/hashes are not new-contract approval. Only demonstrably unsent `prepared` reservations can release their create slot under the workflow lock. Old `creating`/`remote` attempts keep their SKU/ID and can be offered a new processed review only when the retained approved payload and exact draft/backend fingerprint can be verified. Missing/unprovable historical bindings refuse rather than rebind. Old `publishing`, `published` and `published_unverified` attempts become locked `historical_unverified` records: read-only status is available, but no new-contract success badge, automatic PUT or manufactured final approval is possible. Back up the managed directory before upgrading.

Two outcomes prove no listing exists, so the attempt is released for correction and fresh preparation approval under the same SKU: a failure or interruption before any create is sent (for example during image staging; an interrupted attempt is released once no publish is running), or a create rejected with HTTP 401, 403 or 422 (authentication, permission or validation). Timeouts, 5xx, other 4xx responses and empty lookups remain unknown and keep the draft locked for reconciliation.

Back up the entire managed directory, not just the database. Original photos intentionally remain local; keep the directory private and outside source control. `products/`, local managed directories, `.env` and SQLite files are ignored by Git.

## Provider contracts and verification limits

Official references:

- [Personal tokens](https://www.reverb-api.com/docs/authentication) and [scopes](https://www.reverb-api.com/docs/personal-token-scopes-1)
- [Create listings, conditions/categories, explicit shipping and unique inventory](https://www.reverb-api.com/docs/create-listings)
- [Find by SKU, update and publish](https://www.reverb-api.com/docs/updating-your-listing)
- [Image URL ingestion/order](https://www.reverb-api.com/docs/updating-listing-images)
- [Account details](https://www.reverb-api.com/docs/account-details), [sandbox](https://www.reverb-api.com/docs/testing-on-sandbox)
- [R2 S3 operation compatibility, including lifecycle reads](https://developers.cloudflare.com/r2/api/s3/api/)

Reverb ingests accessible HTTPS image URLs. This app does not send localhost/blob URLs or assume a multipart upload endpoint. It uses the documented photo URL array in draft creation, then update/publish and result reads. Unexpected ownership, schemas, missing photos or field differences fail closed rather than pretending success.

**Processed-photo review is not live-provider sign-off.** Retained sandbox evidence demonstrated transformed gallery/cover representations, not equality to uploaded JPEGs. The image guide establishes no immutable-media version or atomic compare-and-publish guarantee. This workflow intentionally reviews processed representations instead of pretending they equal uploads.

SynthShop selects returned ordered photo `url` or HAL `_links.full.href` and independent listing cover `_links.photo.href`; disagreeing `url`/`full` links are refused. It does not invent original endpoints, rewrite CDN paths or strip signature parameters. Numeric IDs are preferred; otherwise tagged exact-URL SHA-256s identify resources without persisting signed locators. Duplicate identities/locators are rejected; equal bytes from distinct legitimate photo entries are allowed.

Each exact returned HTTPS resource is fetched through a separate credential-free, cookie-free, no-proxy client, with no redirects, only on `images.reverb.com` or `rvb-img.reverb.com`. The fixed profile is `Accept: image/jpeg, image/png;q=0.9, image/webp;q=0.8`, `Accept-Encoding: identity`, `User-Agent: SynthShop/processed-photo-review-1`. A nonempty 200, no unexpected content encoding, truthful JPEG/PNG/WebP MIME/format, static full decode, 20 MiB/entity and 40-megapixel limit are required. Bytes are validated, hashed and privately cached **without re-encoding**, then served unchanged through authenticated same-origin, no-store/nosniff routes. Gallery and cover remain independent. Metadata is checked before and after entity reads; this detects observable changes, not an atomic remote snapshot.

Final tokens bind the whole immutable envelope. Fresh comparisons exclude capture/approval times, snapshot/blob IDs and the expected draft-to-live state change, but include selector/profile, listing/shop, ordered identity/relation/locator digest/entity digest/size/type/format/dimensions and independent cover. Benign-looking URL rotation still requires renewed review. No perceptual hash, count, ETag or uploaded-source reference substitutes for that comparison.

Same-profile CDN stability, delayed review after URL expiry, live-transition stability, buyer-surface correspondence and production equivalence remain **unverified** until a separately authorized bounded provider experiment. Deterministic doubles and browser smoke prove local behavior only. Implementation approval is not permission to run that experiment, enable production or publish a real listing.

The two-stage implementation was also exercised in a real isolated Chrome session against the modified local application and an explicitly labeled deterministic offline provider. Preparation stopped at an unpublished draft with one create and zero PUTs; changed processed bytes refused final publication; a fresh separate approval persisted intent before one simulated PUT whose response was lost; read-only reconciliation verified the original snapshot without another PUT. Both returned gallery images and the independent cover decoded in the browser, and bytes fetched from their same-origin routes matched the persisted SHA-256s. No OpenAI/Reverb/R2 calls or real credentials were used. These observations do not qualify any real provider.

At commit `07e63ed86054ac55e0c1dcc730b538125a9eea6c`, before the production create guard, the photo-correspondence correction was exercised through the actual CLI/Chrome review with synthetic photos, isolated storage and a closed HTTP provider double: reversed and substituted first reads sent no publish update; matching bytes in an owner-reordered gallery passed. This proves the application boundary, **not live Reverb ingestion, image transformation behavior, ownership, R2 staging or cleanup compatibility**.

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

Core application services live in `core/application.py` and `core/publishing.py`; FastAPI/Jinja routes live in `web/`; Typer launches the local UI and provides hidden-input OpenAI key setup. There is no native app, multi-user hosting, storefront/Stripe checkout, cross-posting, order management or generalized scraping platform.
