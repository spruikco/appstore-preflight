# App Store Preflight

**Catch the rejection before Apple does.** A pre-submission audit skill for Claude Code
that finds the boring, expensive App Store and Play Store rejections in seconds — before
a submission burns a week in the review queue.

Part of the FAT Agent family (Fix, Audit, Test): where
[FAT Agent](https://github.com/spruikco/fat-agent-skill) audits your deployed website, this audits your
app build and its live store listing.

## Why this exists

Apple's review queue runs days, not hours. Every rejection costs a full cycle. Almost
every check in this skill exists because a real app was really rejected for it:

- A missing `NSCameraUsageDescription` isn't a permission prompt on iOS — it's an
  instant crash the moment the API is touched, found and rejected under 2.1(a).
  A bare `<input type="file" accept="image/*">` counts: iOS offers "Take Photo".
- Screenshots with an **Android status bar** are a 2.3.10 rejection, and the copies
  uploaded in App Store Connect are what get reviewed, not the ones in your repo.
  We got rejected twice for the same five iPad screenshots before making the live
  Media Manager sweep a mandatory step.
- A CI-generated Capacitor project with no icon-generation step ships the default
  robot icon while your listing shows the brand. Play rejects that as Misleading Claims.
- Apps that create accounts must delete them **in-app** (5.1.1(v)). A support email
  is not sufficient, on either store.

## What you get

Two parts, both mandatory:

1. **Static scan** (`preflight.py`) — no build, no simulator, no signing, exit 1 on
   blockers. Privacy usage strings cross-referenced against every place a plist key can
   be declared (including CI configs, which for generated `/ios` dirs are the only
   source of truth). Privacy manifest / required-reason APIs (ITMS-91053). iOS SDK floor,
   deployment target and Play target API level (36 since 31 Aug 2026). Screenshot dimensions and foreign-status-bar detection.
   Placeholder text and dead support/privacy URLs. Account deletion. Sign in with
   Apple. Localhost/staging leftovers. Store parity for CI-generated projects.
2. **Live listing sweep** — the skill walks App Store Connect's Media Manager
   (every device size, every localization) because the uploaded assets are what App
   Review actually sees. Repo-clean does not mean listing-clean.

## Install

Copy the `appstore-preflight` folder into `~/.claude/skills/`, then in Claude Code:

```
/appstore-preflight
```

or just say "preflight this before I submit". The static scan alone:

```bash
python ~/.claude/skills/appstore-preflight/preflight.py <project_dir>
```

Requires Python 3.9+. `pip install Pillow` for the screenshot checks.

## Honest limits

Static analysis cannot run your app. It won't catch a runtime crash on a path it can't
infer, a broken backend, or a layout that falls apart on iPad — install the TestFlight
build and walk the flows App Review walks. Coverage is deepest for hybrid stacks
(Capacitor / Cordova / Expo); pure Swift/Kotlin projects get the plist, manifest,
screenshot and metadata checks but thinner source heuristics.

Perishable facts (accepted screenshot sizes, SDK floors, queue times) are current as of
**September 2026** and commented in the source for easy revision.

## Extending it

`USAGE_RULES` in `preflight.py` maps a source regex to the Info.plist key it demands.
When a rejection reveals a new pattern, add a rule so it can never happen twice. That is
the whole point of the file.
