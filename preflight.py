#!/usr/bin/env python3
"""
App Store / Play Store pre-submission checks.

Static checks only: no simulator, no build, no network beyond a few HEAD requests.
Runs in seconds. The point is to catch the boring, expensive rejections BEFORE a
submission burns a review cycle (days, not hours — 7-11 days as of mid-2026).

Every check here exists because it is a real, common rejection reason. The
usage-string check in particular: on iOS a MISSING purpose string is not a
permission prompt, it is an instant crash the moment the API is touched, which
App Review will find and reject under 2.1(a).

Usage:  python preflight.py [project_dir]
Exit:   0 clean or warnings only, 1 if any FAIL
"""
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")

FAILS: list[str] = []
WARNS: list[str] = []
OKS: list[str] = []


def fail(check, msg, fix=""):
    FAILS.append(f"{check}: {msg}" + (f"\n      fix: {fix}" if fix else ""))


def warn(check, msg, fix=""):
    WARNS.append(f"{check}: {msg}" + (f"\n      fix: {fix}" if fix else ""))


def ok(check, msg):
    OKS.append(f"{check}: {msg}")


def read(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


SKIP_DIRS = {
    "node_modules", ".git", "dist", "build", ".next", "Pods", "vendor",
    "venv", ".venv", "out", ".output", "coverage", "www",
}
# Capacitor/Cordova copy the built web bundle into the native project. Scanning it
# means reading minified dependency code and reporting APIs the app never calls —
# that produced a phantom "Sign in with Apple" failure on an app with only
# email/password auth, because the Supabase SDK ships signInWithOAuth unused.
GENERATED_PATH = re.compile(r"(src[/\\]main[/\\]assets[/\\]public|App[/\\]public|[/\\]www[/\\])", re.I)


def walk_source(exts=(".ts", ".tsx", ".js", ".jsx", ".swift", ".kt", ".java", ".vue", ".html")):
    """First-party source only: no dependencies, no build output, no synced bundles."""
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        if GENERATED_PATH.search(dirpath):
            dirnames[:] = []
            continue
        for fn in filenames:
            if fn.endswith(exts):
                yield os.path.join(dirpath, fn)


def all_source_text():
    return "\n".join(read(p) for p in walk_source())


# ---------------------------------------------------------------- iOS privacy
# API touched in source -> Info.plist key iOS demands before you may touch it.
USAGE_RULES = [
    (
        "NSCameraUsageDescription",
        [
            r"<input[^>]*type=[\"']file[\"'][^>]*accept=[\"'][^\"']*image",
            r"accept=[\"'][^\"']*image[^\"']*[\"'][^>]*type=[\"']file",
            r"getUserMedia\s*\(\s*\{[^}]*video",
            r"@capacitor/camera|Camera\.getPhoto|ImagePicker|launchCamera",
            r"capture=[\"']?(camera|environment|user)",
        ],
        "camera (a file input that offers 'Take Photo' counts)",
    ),
    (
        "NSPhotoLibraryUsageDescription",
        [
            r"<input[^>]*type=[\"']file[\"'][^>]*accept=[\"'][^\"']*image",
            r"accept=[\"'][^\"']*image[^\"']*[\"'][^>]*type=[\"']file",
            # PHPickerViewController is deliberately NOT here: it runs out of
            # process and needs no usage string. Flagging it was a false positive.
            r"@capacitor/camera|photoLibrary|launchImageLibrary|UIImagePickerController",
        ],
        "photo library",
    ),
    (
        "NSLocationWhenInUseUsageDescription",
        [r"@capacitor/geolocation|navigator\.geolocation|getCurrentPosition|watchPosition|CLLocationManager"],
        "location",
    ),
    (
        "NSMicrophoneUsageDescription",
        [r"getUserMedia\s*\(\s*\{[^}]*audio|AVAudioRecorder|@capacitor/voice-recorder"],
        "microphone",
    ),
    (
        "NSContactsUsageDescription",
        [r"@capacitor/contacts|CNContactStore|navigator\.contacts"],
        "contacts",
    ),
    (
        "NSCalendarsUsageDescription",
        [r"EKEventStore|@capacitor/calendar"],
        "calendar",
    ),
    (
        "NSFaceIDUsageDescription",
        [r"LAContext|FaceID|BiometricAuth|@capacitor/biometric"],
        "Face ID",
    ),
    (
        "NSUserTrackingUsageDescription",
        [r"AppTrackingTransparency|requestTrackingAuthorization|idfa"],
        "app tracking (IDFA)",
    ),
]


def plist_sources():
    """Everywhere an Info.plist key might be declared: the plist itself, or CI
    that injects it (a Capacitor/Expo /ios dir is often gitignored and rebuilt,
    so the CI file is frequently the only real source of truth)."""
    texts = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in {"node_modules", ".git", "Pods", "build", "dist"}]
        for fn in filenames:
            if fn == "Info.plist" or fn in (
                "codemagic.yaml", "eas.json", "app.json", "app.config.js", "app.config.ts",
                "Fastfile", "project.pbxproj",
            ):
                texts.append(read(os.path.join(dirpath, fn)))
    for wf in (".github/workflows", ".gitlab-ci.yml", "bitrise.yml"):
        p = os.path.join(ROOT, wf)
        if os.path.isdir(p):
            for fn in os.listdir(p):
                texts.append(read(os.path.join(p, fn)))
        elif os.path.isfile(p):
            texts.append(read(p))
    return "\n".join(texts)


def check_usage_strings():
    src = all_source_text()
    if not src.strip():
        return
    declared = plist_sources()
    if not declared.strip():
        warn("2.1 privacy", "no Info.plist or CI plist injection found; cannot verify usage strings")
        return
    for key, patterns, human in USAGE_RULES:
        used = any(re.search(p, src, re.I) for p in patterns)
        if not used:
            continue
        if key in declared:
            ok("2.1 privacy", f"{human}: {key} declared")
        else:
            fail(
                "2.1 privacy",
                f"app touches {human} but {key} is NOT declared -> iOS kills the app on first use",
                f"add {key} to Info.plist (or to the CI step that writes it)",
            )


# Since 28 Apr 2026 App Store uploads must be built with the iOS 26 SDK (Xcode 26),
# and Xcode 26 cannot target below iOS 15. So the SDK rule drags the deployment
# floor to 15.0 in practice. Capacitor and Expo templates still generate 13.0/14.0,
# so this bites silently on the build machine.
MIN_IOS_DEPLOYMENT_TARGET = 15.0
MIN_XCODE_MAJOR = 26


def check_ios_deployment_target():
    declared = plist_sources()
    found = re.findall(r"IPHONEOS_DEPLOYMENT_TARGET\s*=\s*([0-9.]+)", declared)
    found += re.findall(r"platform\s*:ios,\s*['\"]([0-9.]+)['\"]", declared)
    found += re.findall(r"deploymentTarget[\"']?\s*[:=]\s*[\"']?([0-9.]+)", declared, re.I)
    if found:
        lowest = min(float(v) for v in found if v.replace(".", "").isdigit())
        if lowest < MIN_IOS_DEPLOYMENT_TARGET:
            warn(
                "deployment target",
                f"lowest iOS deployment target is {lowest:g}, below {MIN_IOS_DEPLOYMENT_TARGET:g}",
                "uploads must be built with the iOS 26 SDK since 28 Apr 2026, and Xcode 26 "
                "cannot target below iOS 15; raise it in the Xcode project and Podfile "
                "(patch them in CI if /ios is generated)",
            )
        else:
            ok("deployment target", f"iOS {lowest:g} meets the 15.0 floor")

    # CI pinning an old Xcode fails the SDK rule even with a fine deployment target.
    for m in re.finditer(r"xcode:\s*[\"']?([0-9]+)(?:\.[0-9.]+)?", declared, re.I):
        if int(m.group(1)) < MIN_XCODE_MAJOR:
            warn(
                "SDK floor",
                f"CI pins Xcode {m.group(1)}; uploads must be built with the iOS {MIN_XCODE_MAJOR} SDK "
                f"(Xcode {MIN_XCODE_MAJOR}+) since 28 Apr 2026",
                "bump the xcode version in the CI config",
            )


def check_export_compliance():
    declared = plist_sources()
    if declared.strip() and "ITSAppUsesNonExemptEncryption" not in declared:
        warn(
            "export compliance",
            "ITSAppUsesNonExemptEncryption not set; every upload will prompt for encryption answers",
            "add ITSAppUsesNonExemptEncryption=false if you only use standard HTTPS",
        )


# ---------------------------------------------------- privacy manifest (5.1.2)
# Since May 2024 apps must ship a PrivacyInfo.xcprivacy declaring "required
# reason" API use. UserDefaults, file-timestamp and disk-space APIs all count,
# which in practice means almost every app needs one. Missing/incomplete
# manifests are now one of the most common upload blockers (ITMS-91053).
REQUIRED_REASON_APIS = [
    (r"\bUserDefaults\b|NSUserDefaults", "NSPrivacyAccessedAPICategoryUserDefaults"),
    (r"creationDate|\.fileModificationDate|NSFileCreationDate|NSFileModificationDate", "NSPrivacyAccessedAPICategoryFileTimestamp"),
    (r"systemUptime|mach_absolute_time", "NSPrivacyAccessedAPICategorySystemBootTime"),
    (r"volumeAvailableCapacity|NSFileSystemFreeSize", "NSPrivacyAccessedAPICategoryDiskSpace"),
    (r"activeInputModes", "NSPrivacyAccessedAPICategoryActiveKeyboards"),
]


def check_privacy_manifest():
    manifests = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        manifests += [os.path.join(dirpath, f) for f in filenames if f == "PrivacyInfo.xcprivacy"]

    native_src = "\n".join(read(p) for p in walk_source(exts=(".swift", ".m", ".mm")))
    hits = [cat for pat, cat in REQUIRED_REASON_APIS if re.search(pat, native_src)]

    if manifests:
        text = "\n".join(read(m) for m in manifests)
        missing = [cat for cat in hits if cat not in text]
        if missing:
            fail(
                "5.1.2 privacy manifest",
                f"first-party native code uses required-reason APIs not declared in "
                f"PrivacyInfo.xcprivacy: {', '.join(missing)}",
                "add the category with an approved reason code, or the upload is "
                "rejected with ITMS-91053",
            )
        else:
            ok("5.1.2 privacy manifest", f"PrivacyInfo.xcprivacy present ({len(manifests)} found)")
        return

    # No manifest committed. For CI-generated Capacitor iOS projects the template
    # ships one from @capacitor/ios 6+, so check the dependency floor instead.
    pkg = read(os.path.join(ROOT, "package.json")) or "\n".join(
        read(os.path.join(dp, "package.json"))
        for dp, dn, fs in os.walk(ROOT)
        if "package.json" in fs and "node_modules" not in dp
    )
    m = re.search(r"\"@capacitor/ios\"\s*:\s*\"[^0-9]*([0-9]+)", pkg)
    if m and int(m.group(1)) >= 6:
        ok("5.1.2 privacy manifest", f"@capacitor/ios {m.group(1)}.x template ships PrivacyInfo.xcprivacy")
    elif hits:
        fail(
            "5.1.2 privacy manifest",
            f"no PrivacyInfo.xcprivacy anywhere, but native source uses required-reason "
            f"APIs ({', '.join(hits)})",
            "create one declaring each category with an approved reason code (Apple's "
            "'Describing use of required reason API' doc lists the codes)",
        )
    else:
        warn(
            "5.1.2 privacy manifest",
            "no PrivacyInfo.xcprivacy found and could not confirm the build template "
            "provides one",
            "verify the built .app contains a privacy manifest; uploads without one "
            "draw ITMS-91053 warnings that become rejections",
        )


# Google Play target API level. Since 31 Aug 2026 new apps and updates must target
# Android 16 (API 36); existing apps below API 35 are hidden from new users on newer
# devices. An extension to 1 Nov 2026 can be requested in Play Console. Google raises
# this floor every August, so revisit MIN_ANDROID_TARGET_SDK each September.
MIN_ANDROID_TARGET_SDK = 36
# Capacitor's android template sets targetSdk in android/variables.gradle; when
# android/ is CI-generated the pinned @capacitor/android major is the only source of
# truth. Template defaults per major, as shipped.
CAPACITOR_ANDROID_TARGET = {5: 33, 6: 34, 7: 35, 8: 36}


def gradle_sources():
    texts = []
    for dp, dns, fs in os.walk(ROOT):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fs:
            if fn.endswith((".gradle", ".gradle.kts")) or fn in (
                "codemagic.yaml", "codemagic.yml", "eas.json", "app.json", "app.config.js",
                "app.config.ts", "gradle.properties",
            ):
                texts.append(read(os.path.join(dp, fn)))
    return "\n".join(texts)


def check_android_target_sdk():
    declared = gradle_sources()
    pkg = read(os.path.join(ROOT, "package.json"))
    cap_android = re.search(r'"@capacitor/android"\s*:\s*"[^0-9]*([0-9]+)', pkg)
    if not declared.strip() and not cap_android:
        return  # no Android project to judge
    found = [int(v) for v in re.findall(
        r"targetSdk(?:Version)?\s*[=:]?\s*\(?\s*[\"']?([0-9]{2})", declared)]
    if found:
        lowest = min(found)
        if lowest < MIN_ANDROID_TARGET_SDK:
            fail(
                "Play target API",
                f"lowest targetSdk is {lowest}, below {MIN_ANDROID_TARGET_SDK}",
                f"Play rejects new apps and updates targeting below API {MIN_ANDROID_TARGET_SDK} "
                "since 31 Aug 2026; raise targetSdkVersion (android/variables.gradle for "
                "Capacitor, app/build.gradle otherwise) and re-test on Android 16",
            )
        else:
            ok("Play target API", f"targetSdk {lowest} meets the {MIN_ANDROID_TARGET_SDK} floor")
        return
    if cap_android:
        major = int(cap_android.group(1))
        default = CAPACITOR_ANDROID_TARGET.get(major)
        if default is not None and default < MIN_ANDROID_TARGET_SDK:
            fail(
                "Play target API",
                f"@capacitor/android {major} template targets API {default} and no targetSdk "
                f"override is committed; CI-generated android/ will ship below {MIN_ANDROID_TARGET_SDK}",
                "upgrade @capacitor/android (8+ targets 36) or add a CI step that rewrites "
                "targetSdkVersion in android/variables.gradle after `cap add android`",
            )
        elif default is None:
            warn("Play target API",
                 f"@capacitor/android {major}: unknown template default; confirm targetSdk >= "
                 f"{MIN_ANDROID_TARGET_SDK} in android/variables.gradle on the build machine")
        else:
            ok("Play target API", f"@capacitor/android {major} template targets API {default}")
    else:
        warn("Play target API",
             f"Android config found but no targetSdk declaration; confirm it is >= "
             f"{MIN_ANDROID_TARGET_SDK} in app/build.gradle before uploading")


# ------------------------------------------------------------- screenshots
# Accepted App Store Connect dimensions as of Aug 2026. Only the largest size per
# family (6.9" iPhone, 13" iPad) is REQUIRED — ASC scales the rest — but any
# uploaded file must still match one of these exactly. Revisit each September.
APPLE_SIZES = {
    (1320, 2868), (2868, 1320), (1290, 2796), (2796, 1290),
    (1260, 2736), (2736, 1260),
    (1284, 2778), (2778, 1284), (1242, 2688), (2688, 1242),
    (1242, 2208), (2208, 1242), (1179, 2556), (2556, 1179),
    (1170, 2532), (2532, 1170),
    (2064, 2752), (2752, 2064),
    (2048, 2732), (2732, 2048), (1668, 2388), (2388, 1668),
}


def check_screenshots():
    try:
        from PIL import Image
    except ImportError:
        warn("2.3.10 screenshots", "Pillow not installed, skipping image checks", "pip install Pillow")
        return

    shots = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        if GENERATED_PATH.search(dirpath):
            dirnames[:] = []
            continue
        # Archived captures are kept for reference and are not what gets submitted.
        if re.search(r"(orig|backup|archive|old|raw)[/\\]?$|[/\\](orig|backup|archive|old|raw)", dirpath, re.I):
            continue
        if re.search(r"screenshot|store-assets|fastlane|metadata|ios", dirpath, re.I):
            for fn in filenames:
                if fn.lower().endswith((".png", ".jpg", ".jpeg")) and re.search(r"screen|shot|ios-\d|\d{2}-", fn, re.I):
                    shots.append(os.path.join(dirpath, fn))
    if not shots:
        warn("2.3.10 screenshots", "no screenshots found to check")
        return

    for path in sorted(shots):
        rel = os.path.relpath(path, ROOT)
        # Play screenshots legitimately show an Android status bar and use Android
        # sizes. Only hold the iOS set to Apple's rules.
        if re.search(r"android|play", rel, re.I):
            continue
        try:
            im = Image.open(path).convert("RGB")
        except Exception as e:
            warn("2.3.10 screenshots", f"{rel}: unreadable ({e})")
            continue
        w, h = im.size
        if (w, h) not in APPLE_SIZES:
            warn("2.3.10 screenshots", f"{rel}: {w}x{h} is not a standard App Store size")

        # Foreign status bar: a full-width band at the very top whose colour is
        # near-neutral grey/black and clearly different from the content beneath.
        # Android renders exactly this; an iOS screenshot's bar sits on the app's
        # own background. Reported for a human to eyeball, not auto-failed.
        band = None
        prev = None
        for y in range(0, min(int(h * 0.06), 200)):
            row = [im.getpixel((x, y)) for x in range(0, w, max(1, w // 24))]
            avg = tuple(sum(c[i] for c in row) // len(row) for i in range(3))
            if prev is not None and max(abs(a - b) for a, b in zip(avg, prev)) > 30:
                band = (y, prev)
                break
            prev = avg
        if band:
            y, colour = band
            r, g, b = colour
            neutral = max(r, g, b) - min(r, g, b) < 18
            # Measured Android status bars average 119-144 brightness; light app
            # backgrounds (cream headers etc.) sit around 200+. 170 splits them
            # with margin both ways — below it, treat as foreign chrome.
            darkish = sum(colour) / 3 < 170
            if neutral and darkish and y >= 20:
                fail(
                    "2.3.10 screenshots",
                    f"{rel}: solid {colour} bar across the top {y}px looks like a non-iOS status bar",
                    "Apple rejects screenshots showing another platform's status bar; "
                    "recapture on an iOS simulator or paint the strip out",
                )


# ------------------------------------------------------------ store metadata
def http_ok(url):
    try:
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": "preflight/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except Exception as e:
        return f"ERR {e}"


def check_metadata():
    listing = ""
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in {"node_modules", ".git"}]
        for fn in filenames:
            if re.search(r"store.?listing|metadata|listing", fn, re.I) and fn.endswith((".md", ".txt", ".json")):
                listing += read(os.path.join(dirpath, fn))
    if not listing.strip():
        warn("metadata", "no store listing file found to check")
        return

    for ph in ("lorem ipsum", "TODO", "TBD", "example.com", "FIXME", "XXX", "placeholder"):
        if re.search(re.escape(ph), listing, re.I):
            fail("2.3 metadata", f"listing contains placeholder text {ph!r}", "replace before submitting")

    urls = sorted(set(re.findall(r"https?://[^\s)\]\"'>]+", listing)))
    for u in urls[:14]:
        u = u.rstrip(".,")
        if not re.search(r"privacy|terms|support|contact|policy|\.com/?$|\.co/?$", u, re.I):
            continue
        status = http_ok(u)
        if status == 200:
            ok("metadata", f"{u} -> 200")
        else:
            fail("2.3 metadata", f"{u} -> {status}", "App Review clicks these; a dead support or privacy URL is a rejection")


# ------------------------------------------------- account deletion (5.1.1v)
def check_account_deletion():
    src = all_source_text()
    if not re.search(r"signUp|createUser|register|signInWithPassword", src, re.I):
        return
    if re.search(
        r"delete[-_ ]?account|deleteUser|/delete-account|close[-_ ]?account|"
        r"remove[-_ ]?account|account[-_ ]?delet(e|ion)|deactivate[-_ ]?account",
        src, re.I,
    ):
        ok("5.1.1(v)", "in-app account deletion present")
    else:
        fail(
            "5.1.1(v) account deletion",
            "app creates accounts but no in-app deletion path found "
            "(searched delete/close/remove/deactivate-account naming)",
            "Apple requires account deletion from inside the app, not just a support "
            "email. If yours exists under another name, this is a false positive — "
            "confirm by walking the flow, and consider the conventional naming",
        )


def check_sign_in_with_apple():
    src = all_source_text()
    social = re.search(r"signInWithOAuth|GoogleSignIn|FacebookLogin|provider:\s*[\"'](google|facebook|twitter)", src, re.I)
    apple = re.search(r"apple", src, re.I) and re.search(r"signInWithApple|provider:\s*[\"']apple", src, re.I)
    if social and not apple:
        fail(
            "4.8 Sign in with Apple",
            "third-party social login present with no Sign in with Apple option",
            "Apple requires an equivalent Apple login alongside Google/Facebook logins",
        )


def check_debug_leftovers():
    hits = []
    for p in walk_source():
        t = read(p)
        for m in re.finditer(r"https?://(localhost|127\.0\.0\.1|10\.0\.2\.2|.*\.ngrok\.[a-z]+|.*staging[^\s\"']*)", t):
            hits.append(f"{os.path.relpath(p, ROOT)}: {m.group(0)}")
    for h in hits[:8]:
        warn("2.1 completeness", f"local/staging URL in shipped source: {h}")


def check_store_parity():
    """Play 'Misleading Claims' / App Review 2.3: the INSTALLED app must match
    the listing — icon, name, splash. Born from a real rejection (Manager 11,
    8 Aug 2026): the Capacitor android/ project is regenerated on CI, iOS had
    an icon-generation step and Android didn't, so Play reviewers installed
    the default Capacitor robot icon while the listing showed the brand.
    Static rule: if a Capacitor platform dir is NOT committed (i.e. CI runs
    `cap add`), the CI config MUST run `@capacitor/assets generate` for that
    platform — otherwise the build ships placeholder branding by definition."""
    caps = [os.path.join(dp, "capacitor.config.ts") for dp, _, fs in os.walk(ROOT)
            if "node_modules" not in dp and "capacitor.config.ts" in fs]
    if not caps:
        return
    ci_text = ""
    for name in ("codemagic.yaml", "eas.json", ".github"):
        p = os.path.join(ROOT, name)
        if os.path.isfile(p):
            ci_text += read(p)
        elif os.path.isdir(p):
            for dp, _, fs in os.walk(p):
                for f in fs:
                    if f.endswith((".yml", ".yaml")):
                        ci_text += read(os.path.join(dp, f))
    for cap in caps:
        mob = os.path.dirname(cap)
        for platform in ("android", "ios"):
            committed = os.path.isdir(os.path.join(mob, platform, "app" if platform == "android" else "App"))
            gen = re.search(r"@capacitor/assets\s+generate[^\n]*(--" + platform + r"|\ngenerate\b)", ci_text) or \
                  re.search(r"@capacitor/assets\s+generate(?!\s+--)", ci_text)
            if not committed and not gen:
                fail("store parity",
                     f"{platform}: platform dir is CI-generated but no '@capacitor/assets generate --{platform}' "
                     f"step found in CI — the build will ship the DEFAULT Capacitor icon/splash. "
                     f"Play rejects this as Misleading Claims (listing/installed mismatch).")
            else:
                ok("store parity", f"{platform} icon generation covered")
    # Name parity is judgement, not grep — remind the human.
    warn("store parity",
         "verify by INSTALLING the store-track build on a device/emulator: home-screen icon, "
         "launcher name and first-launch splash must match the store listing exactly. "
         "A signed, valid artifact can still wear the wrong face.")
    warn("store parity",
         "walk the LAUNCH SEQUENCE on an emulator: Android 12+ system splash -> "
         "Capacitor/native splash -> WebView first paint must all share one background "
         "colour and mark (no white flash, no template icon). The system splash is "
         "styled by windowSplashScreenBackground/AnimatedIcon in the LAUNCH theme — "
         "if the native project is CI-generated, CI must patch those in.")


# ----------------------------------------- Play Data Safety (User Data policy)
# Google runs the uploaded build and watches the network. If a data type leaves
# the device that the Data Safety form doesn't declare, the update is rejected
# with "Invalid Data safety form" — the live app stays up, but the update is dead
# until the form is fixed and resent (a full review cycle, ~1-3 days).
#
# The classic, silent miss: ANY analytics/CRM SDK (GA4, Firebase, Klaviyo, Segment,
# Amplitude...) derives an APPROXIMATE LOCATION from the caller's IP address and
# sends it to a third party. So an app whose developer "isn't using location" still
# transmits location, and the form says it collects none. Cost a Play review cycle
# on 13 Aug 2026 (a client's incident-reporting app, version code 25): GA4 transmitted
# Approximate Location, undeclared -> "Location Data Type - Approximate Location".
#
# The form lives in Play Console, not the repo, so this can only flag what the app
# demonstrably transmits and force a human to reconcile it against the live form.
# Each (regex, SDK name, [data types Google will see], destination).
DATA_TRANSMITTERS = [
    (r"gtag\s*\(|googletagmanager|G-[A-Z0-9]{9,}|firebase[/-]analytics|@capacitor-community/firebase-analytics|\breact-ga\b|ReactGA",
     "Google Analytics / Firebase Analytics",
     ["Location > Approximate location (derived from IP)", "App activity", "Device or other IDs"],
     "Google"),
    (r"klaviyo",
     "Klaviyo",
     ["Personal info > Email address", "App activity", "Device or other IDs",
      "Location > Approximate location (from IP)"],
     "Klaviyo (third party)"),
    (r"api\.mapbox\.com|mapbox|maptiler|tile\.openstreetmap|maps\.googleapis\.com",
     "Map tiles / geocoding",
     ["Location > Approximate or Precise location (coordinates sent for tiles/geocoding)"],
     "the map provider"),
    (r"\bsentry\b|bugsnag|crashlytics",
     "Crash / diagnostics SDK",
     ["App info & performance > Crash logs / Diagnostics", "Device or other IDs"],
     "the diagnostics provider"),
    (r"mixpanel|amplitude|analytics\.segment|cdn\.segment|posthog",
     "Third-party product analytics",
     ["App activity", "Device or other IDs", "Location > Approximate location (from IP)"],
     "the analytics provider"),
]

# Location ACCESSED on-device. Access is not the same as transmission, but combined
# with any networking SDK it usually becomes "collected" in Play's sense.
LOCATION_ACCESS = (
    r"@capacitor/geolocation|navigator\.geolocation|getCurrentPosition|watchPosition|"
    r"CLLocationManager|FusedLocationProvider"
)


def check_data_safety():
    """Play User Data policy: the Data Safety form must match what actually leaves
    the device. Analytics/CRM/map SDKs transmit more than developers expect — above
    all an IP-derived approximate location — and the mismatch is an instant rejection.
    Can't read the live form, so warn with the concrete list to reconcile against
    Play Console -> App content -> Data safety."""
    src = all_source_text()
    if not src.strip():
        return

    detected = [(name, types, dest) for pat, name, types, dest in DATA_TRANSMITTERS
                if re.search(pat, src, re.I)]

    if not detected:
        if re.search(LOCATION_ACCESS, src, re.I):
            warn("Play Data Safety",
                 "app accesses device location; confirm whether it ever leaves the device "
                 "(map tiles, geocoding, logging, analytics) and declare it if so")
        return

    lines = [f"        - {name} (shares with {dest}): " + "; ".join(types)
             for name, types, dest in detected]
    warn(
        "Play Data Safety",
        "data-transmitting SDK(s) detected. Google's scanner WILL see these data types "
        "leave the device, and every one must be declared COLLECTED and SHARED in the "
        "Play Data Safety form or the upload is rejected 'Invalid Data safety form':\n"
        + "\n".join(lines)
        + "\n        MOST COMMON MISS: analytics SDKs transmit an APPROXIMATE LOCATION from "
        "the IP address even when the app never asks for GPS.",
        "Play Console -> App content -> Data safety: declare each data type above "
        "(collected + shared, with a purpose), keep it consistent with the privacy "
        "policy, then resend on Publishing overview. This form is console-only — a clean "
        "static run does NOT clear it; verify it against the live form every submission.",
    )
    if re.search(LOCATION_ACCESS, src, re.I):
        warn("Play Data Safety",
             "app also accesses precise device location (GPS); if any coordinate is sent "
             "off-device (maps, geocoding, a backend) declare Precise location as well")


# ------------------------------------------------- in-app purchases (2.1 / 3.1.1)
# The failure this check exists for (Manager 11, 2 Sep 2026): the app shipped a
# store screen that renders products returned by StoreKit/Play Billing, the four
# products existed in App Store Connect since 7 Aug — and were NEVER SUBMITTED
# for review. Production StoreKit returns an EMPTY product list for anything not
# APPROVED, with no error anywhere, so the only symptom was the in-app store
# saying "unavailable" while every client-side fix chased a phantom bug.
# Product review state lives server-side; no static scan can see it, so this
# check detects that the app sells IAPs at all and demands the live state check.
IAP_CLIENT = re.compile(
    r"(StoreKit|Transaction\.updates|SKPaymentQueue|BillingClient|queryProductDetails"
    r"|querySkuDetails|capacitor-iap|cordova-plugin-purchase|react-native-iap"
    r"|expo-in-app-purchases|revenuecat|purchases-)", re.I)


def check_iap_states():
    src = all_source_text()
    if not src.strip() or not IAP_CLIENT.search(src):
        return
    warn(
        "IAP review state (3.1.1)",
        "the app sells in-app purchases. Their review state CANNOT be verified from "
        "the repo, and products that are not APPROVED are silently absent from "
        "production StoreKit / Play Billing — the store UI renders empty or "
        "'unavailable' with zero errors while sandbox works fine",
        "MANDATORY live check before every submission (see SKILL.md, 'In-app "
        "purchases are their own review pipeline'): pull every product's state from "
        "the App Store Connect API and require APPROVED (or WAITING_FOR_REVIEW when "
        "in flight); on Play confirm every product is ACTIVE in Monetize -> Products. "
        "Apple gotchas that already cost a cycle: the FIRST consumable can only be "
        "reviewed attached to an app version submission "
        "(FIRST_CONSUMABLE_MUST_BE_SUBMITTED_ON_VERSION), and a build only attaches "
        "to a version whose version string matches the binary's "
        "CFBundleShortVersionString — regenerated Capacitor/Expo ios/ resets it.",
    )


def main():
    # Windows consoles default to cp1252; source-derived text (SDK names,
    # listing copy) can carry glyphs it cannot encode and a crashed report
    # is worse than a mangled character.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(f"App Store preflight  ->  {ROOT}\n")
    check_usage_strings()
    check_ios_deployment_target()
    check_android_target_sdk()
    check_export_compliance()
    check_privacy_manifest()
    check_screenshots()
    check_metadata()
    check_account_deletion()
    check_sign_in_with_apple()
    check_debug_leftovers()
    check_store_parity()
    check_data_safety()
    check_iap_states()

    if OKS:
        print("PASS")
        for o in OKS:
            print(f"  [ok]   {o}")
        print()
    if WARNS:
        print("WARNINGS")
        for w in WARNS:
            print(f"  [warn] {w}")
        print()
    if FAILS:
        print("BLOCKERS  (these get you rejected)")
        for f in FAILS:
            print(f"  [FAIL] {f}")
        print()

    print(f"{len(FAILS)} blockers, {len(WARNS)} warnings, {len(OKS)} passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
