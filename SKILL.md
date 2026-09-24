---
name: appstore-preflight
description: Pre-submission audit for iOS/Android app builds against the App Store Review Guidelines and Play policy. Two parts, both mandatory: a static repo scan, and a live sweep of App Store Connect Media Manager screenshots AND the Play Data Safety form against what the app actually transmits. Catches the expensive, boring rejections (missing Info.plist usage strings that crash on launch, foreign status bars in screenshots, dead support URLs, missing account deletion, Sign in with Apple, in-app purchase products sitting unsubmitted/unapproved in App Store Connect so production StoreKit serves an empty store, and an Invalid Data Safety form where an analytics SDK ships undeclared approximate location) BEFORE burning a review cycle. Use before ANY submission or resubmission to App Store Connect or Play Console, when an app has been rejected and you need to find what else would fail, or when the user asks to QA/check/audit an app build prior to release. Triggers on "preflight", "check before submitting", "app store QA", "will this get rejected", "review guidelines check", "data safety".
---

# App Store preflight

Apple's review queue has been running **7 to 11 days**. Every rejection costs another
full cycle, so the goal is to find everything statically, in seconds, before submitting.

## Run it

```bash
python ~/.claude/skills/appstore-preflight/preflight.py <project_dir>
```

Exit code 1 if there are blockers, 0 otherwise. No build, no simulator, no signing.

Report the blockers to the user grouped by guideline, fix them, then re-run until clean.
Do not tell the user a submission is ready on the basis of anything less than a clean run,
and be explicit that a clean run is necessary, not sufficient — it cannot catch a runtime
crash on a path it can't see, judge whether screenshots represent the app honestly, or see
what is actually uploaded in App Store Connect. After a clean run, do the live Media
Manager sweep below before calling anything ready.

## What it checks, and why each one exists

**2.1(a) crashes — privacy usage strings.** The highest-value check. On iOS a *missing*
purpose string is not a permission prompt, it is an immediate process kill. The script
greps first-party source for APIs that require one (camera, photo library, location,
mic, contacts, calendar, Face ID, ATT) and cross-references every place a key could be
declared: `Info.plist`, `codemagic.yaml`, `eas.json`, `app.json`, Fastfile, CI workflows.

This matters most for Capacitor/Expo projects where `/ios` is gitignored and regenerated
on the build machine — the CI file is then the *only* real source of truth, and it is easy
to add a plugin without adding its usage string. A bare
`<input type="file" accept="image/*">` counts: iOS offers "Take Photo" from it and will
kill the app if `NSCameraUsageDescription` is absent.

**5.1.2 privacy manifest.** Since May 2024 every app effectively needs a
`PrivacyInfo.xcprivacy` — UserDefaults alone counts as a "required reason" API, and a
missing or incomplete manifest draws ITMS-91053 at upload. The check finds committed
manifests and cross-references them against required-reason APIs in first-party native
source; for CI-generated Capacitor projects it verifies `@capacitor/ios` 6+, whose
template ships one.

**SDK floor / deployment target.** Since 28 Apr 2026 uploads must be built with the
iOS 26 SDK, and Xcode 26 cannot target below iOS 15. Flags deployment targets under 15.0
and CI configs pinning Xcode below 26 — Capacitor/Expo templates still generate 13.0/14.0,
so this bites silently on the build machine.

**Play target API level.** Since 31 Aug 2026 new apps and updates must target Android 16
(API 36), and existing apps below API 35 are hidden from new users on newer devices. Flags
any committed `targetSdk` under 36; for CI-generated Capacitor projects it reads the pinned
`@capacitor/android` major, because that template's `variables.gradle` is what actually
ships (7 targets 35, 8 targets 36). Google raises this floor every August.

**2.3.10 metadata — screenshots.** Flags non-standard dimensions, and detects a solid
near-neutral bar across the top of an iOS screenshot, which is what an Android status bar
looks like (measured bars run 119-144 brightness; anything under 170 is treated as foreign
chrome, so light app-background headers don't false-positive). Apple rejects screenshots
showing another platform's chrome. Android/Play screenshots are skipped, as are
`*-orig`/`backup`/`archive` folders. Only the largest size per family (6.9" iPhone,
13" iPad) is required as of 2026 — ASC scales the rest — but any uploaded file must still
match an accepted dimension exactly.

**2.3 metadata — placeholders and URLs.** Greps listing copy for `TODO`, `lorem ipsum`,
`example.com` and friends, and GETs the privacy/terms/support URLs. App Review clicks
those links; a dead one is a rejection.

**5.1.1(v) account deletion.** If the app creates accounts, there must be an in-app
deletion path. A support email is not sufficient and both stores enforce it.

**4.8 Sign in with Apple.** Third-party social login without an Apple equivalent is a
rejection. Only first-party source is scanned — a synced web bundle contains unused SDK
code and will produce phantom hits.

**2.1 completeness.** localhost / ngrok / staging URLs left in shipped source.

**Export compliance.** `ITSAppUsesNonExemptEncryption`, so uploads stop prompting.

**Play Data Safety — the form must match what actually leaves the device.** Google
runs the uploaded build and watches the network; a data type transmitted off-device but
not declared in the Data Safety form is an instant "Invalid Data safety form" rejection.
The silent killer is that **every analytics/CRM SDK (GA4, Firebase, Klaviyo, Segment,
Amplitude) derives an approximate location from the caller's IP and sends it to a third
party** — so an app whose developer "isn't using location" transmits location anyway, and
the form says it collects none. The scan greps first-party source for these SDKs plus map/
geocoding and crash providers, and lists the exact data types (location, app activity,
device IDs, email) that Google will see leave the device and that must therefore be
declared collected **and** shared. This cost a Play review cycle on 13 Aug 2026 (a
client's incident-reporting app, version code 25): GA4 transmitted Approximate Location, the form
declared no location, rejected. It is a warning, not a hard fail, because the form lives in
Play Console and cannot be read from the repo — which is exactly why the live check below is
mandatory.

## The live listing is what gets reviewed — audit Media Manager, not just the repo

The static scan only sees files on disk. App Review sees what is **uploaded in App Store
Connect**, and the two drift: fixing the local screenshot set does nothing to the copies
already sitting in Media Manager. This exact gap cost a review cycle on 12 Aug 2026 —
Banh Mi Locator's repo screenshots were clean, the iPhone 6.5" uploads were clean, but the
iPad 13" uploads were still the old Android-status-bar captures, and Apple reviewed on an
iPad. A clean `preflight.py` run therefore does NOT clear this section; it must be done
against the live listing every time.

Using the Chrome tools (or asking the user for screenshots of the pages if the browser is
unavailable):

1. Open `https://appstoreconnect.apple.com/apps/<appId>/distribution/ios/version/inflight/media-manager/iphone`
   and the matching `/ipad` page. **Check the provider name top-right first** — accounts
   on multiple ASC teams silently flip provider and 404 the app.
2. Walk **every** device-size row on both tabs, including collapsed ones (expand each
   chevron). A row is only safe if it either shows the new uploads or says
   "Using N" Display" pointing at a size you have already verified. An expanded row with
   its own stale uploads is exactly the failure mode this section exists for.
3. Zoom into the **top strip of every thumbnail**. Android chrome reads as a dark
   full-width band with a clock on the left and triangle/battery glyphs on the right.
   Any status bar that isn't the app's own background is a rejection; blank/cream is fine.
4. Confirm count and order match the primary set, and that localizations other than the
   primary don't carry their own stale uploads (the language picker, top right).

5. **Age rating questionnaire (mandatory since September 2026).** Apple's expanded age
   rating questions must be answered on every new app or update submission, including the
   new social-media and AI-chatbot questions; an unanswered questionnaire blocks "Add for
   Review". Open the App Information page, confirm the rating is set and matches the app's
   actual content, and note the Social Media descriptor if the app has any feed of
   user-generated content.

To replace a bad set: Delete All on that size row, then upload replacements **one at a
time, waiting for each to process** — concurrent uploads land in completion order, which
scrambles the sequence. iPad 13" accepts 2048x2732; padding a clean phone capture onto
that canvas with the app's background colour is acceptable and has passed review.

## Play Data Safety is console-only — verify the live form every time

Like the screenshots, the Data Safety declaration is not in the repo; it lives in Play
Console and drifts from reality as SDKs are added. The static scan lists what the app
transmits, but only the live form proves what is declared, so reconcile them before every
Play submission:

1. Play Console -> **App content -> Data safety**. Read the current declaration.
2. For **every** SDK the scan flagged (and any not flagged), confirm its data types are
   declared **collected AND shared**, with a purpose. Analytics/CRM SDKs (GA4, Firebase,
   Klaviyo, Segment) mean, at minimum: **Approximate location** (from IP), **App activity**,
   **Device or other IDs**; Klaviyo and profile forms add **Email address / Name**; maps and
   geocoding can add **Precise location**. The report data that stays on the device and is
   shared by the user (photos, the GPS pin in an emailed PDF) is *not* "collected" — only
   what your code sends off-device counts.
3. Confirm the form is consistent with the **privacy policy** URL on the listing; Google
   cross-checks them.
4. If you changed it, **Publishing overview -> send changes for review**. A Data Safety fix
   needs no new binary; it is a form resubmission and re-reviews in ~1-3 days.

The rejection that motivated this section (13 Aug 2026) was purely the form: the 1.1.14
binary was fine, but GA4's undeclared approximate location killed the update while the
previous version stayed live.

## In-app purchases are their own review pipeline — check product states live

The binary and the products are reviewed SEPARATELY. An app can be READY_FOR_SALE with a
perfect store screen while every product sits unsubmitted — and production StoreKit / Play
Billing return an **empty product list with no error** for anything not approved, so the
only user-visible symptom is a store that says "unavailable" while sandbox works. This cost
Manager 11 nearly a month of live revenue (products created 7 Aug 2026, discovered
unsubmitted 2 Sep) with the client bug-hunted twice in between. State lives server-side;
no repo scan can see it. Check it live before AND after every store submission:

1. **Apple — pull every product's state** (App Store Connect API, authenticated with an
   App Store Connect API key JWT):
   `GET /v1/apps/<appId>/inAppPurchasesV2?fields[inAppPurchases]=name,productId,state`.
   Healthy is `APPROVED` (or `WAITING_FOR_REVIEW` mid-flight). `READY_TO_SUBMIT`,
   `MISSING_METADATA`, `DEVELOPER_REJECTED` all mean invisible in production. Do not
   infer state from the client, sandbox, or the products "existing" in the ASC UI.
2. **The first consumable cannot be submitted alone.** Apple enforces
   `FIRST_CONSUMABLE_MUST_BE_SUBMITTED_ON_VERSION`: the review submission must also carry
   an app version. If the live version is already released, create a new version string
   (1.0.1) purely as the vehicle. The working API sequence: POST
   `/v1/reviewSubmissionItems` with relationship `inAppPurchaseVersion` (NOT
   `inAppPurchaseV2`) for each product against an open reviewSubmission — this alone
   flips stuck `DEVELOPER_REJECTED` versions to `READY_FOR_REVIEW` — then attach a build
   to the new appStoreVersion, add it as another item, PATCH the submission
   `{submitted: true}`, and re-run step 1 expecting every product `WAITING_FOR_REVIEW`.
3. **Build/version matching.** A build only attaches to a version whose string equals the
   binary's `CFBundleShortVersionString`. Regenerated native projects (`cap add ios`)
   reset it to 1.0 on every CI run — pin the marketing version in the CI build step next
   to the build-number bump or ASC will refuse every build you produce.
4. **Play — products must be ACTIVE**: Play Console -> Monetize -> Products -> In-app
   products. ACTIVE products serve immediately (no per-product review like Apple), so an
   empty product list on Android is a client/billing-connection problem, not a state one.
5. **After approval, verify from a production device** (not sandbox): the store screen
   must list the products. Only then report the purchase rail live.

## What it deliberately does not do

It cannot run the app. It will not catch a crash on a code path it can't infer, a broken
backend, or a UI that fails on iPad. For those, install the TestFlight build and actually
walk the flows App Review walks — sign up, edit profile, take a photo, post a review,
delete the account — on both an iPhone and an iPad.

Coverage is deepest for hybrid apps (Capacitor, Cordova, Expo/React Native) where the
web bundle and CI config are the source of truth. Pure Swift/Kotlin projects get the
plist, manifest, screenshot, metadata and URL checks, but the source-scanning heuristics
know fewer native idioms — treat a clean run there with proportionally more suspicion.

## Extending it

`USAGE_RULES` in `preflight.py` maps a source regex to the Info.plist key it demands.
When a rejection reveals a new pattern, add a rule so it can never happen twice. That is
the whole point of the file.
