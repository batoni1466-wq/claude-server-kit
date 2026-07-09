#!/usr/bin/env python3
# Patch CloudCLI (@cloudcli-ai/cloudcli 1.36.0). Idempotent, keeps .orig backups.
# Does three groups of front-end/back-end edits:
#   (a) chat paperclip accepts any file type (not just images), up to 20 / 25MB;
#   (b) remove the top tabs Shell / Git (Source Control) / Browser — leave Chat + Files;
#   (c) remove the sidebar version/update block and never report an update as available
#       (so nobody can trigger `npm install -g ... @latest` from the UI and wipe the fork).
# Ends with a content-hash cache-bust of the JS bundle (rename + fix index.html) so an
# already-running server's browsers/service-worker pick up the change. Cache-bust is a
# no-op on a fresh install (bundle just gets its hashed name) and idempotent on re-run.
# Usage: python3 patch-attachments.py [INSTALL_DIR]
import glob, hashlib, os, re, sys

BASE = sys.argv[1] if len(sys.argv) > 1 else '/usr/lib/node_modules/@cloudcli-ai/cloudcli'
report = []

def patch(path, repls):
    with open(path, encoding='utf-8') as f:
        s = f.read()
    orig = s
    for old, new, expect in repls:
        c = s.count(old)
        if c == 0:
            if new == '' or new in s:   # removal already done, or replacement already present
                report.append(f"  skip (already): {old[:45]!r}")
                continue
            raise SystemExit(f"FAIL: not found: {old[:70]!r} in {os.path.basename(path)}")
        if expect is not None and c != expect:
            raise SystemExit(f"FAIL: found {c}x expected {expect}: {old[:60]!r}")
        s = s.replace(old, new)
        report.append(f"  ok {c}x: {old[:45]!r}")
    if s != orig:
        bak = path + '.orig'
        if not os.path.exists(bak):
            open(bak, 'w', encoding='utf-8').write(orig)
            report.append(f"  backup -> {os.path.basename(bak)}")
        open(path, 'w', encoding='utf-8').write(s)
        report.append(f"  WROTE {os.path.basename(path)}")
    else:
        report.append(f"  no change {os.path.basename(path)}")

# ---- server: index.js (upload endpoint) ----
server = os.path.join(BASE, 'dist-server/server/index.js')
patch(server, [
    ("""        const fileFilter = (req, file, cb) => {
            const allowedMimes = ['image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/svg+xml'];
            if (allowedMimes.includes(file.mimetype)) {
                cb(null, true);
            }
            else {
                cb(new Error('Invalid file type. Only JPEG, PNG, GIF, WebP, and SVG are allowed.'));
            }
        };""",
     """        const fileFilter = (req, file, cb) => {
            cb(null, true);
        };""", 1),
    ("fileSize: 5 * 1024 * 1024, // 5MB", "fileSize: 25 * 1024 * 1024, // 25MB", None),
    ("upload.array('images', 5)", "upload.array('images', 20)", 1),
    ("                files: 5\n            }", "                files: 20\n            }", 1),
])

# ---- server: claude-sdk.js (prompt label) ----
sdk = os.path.join(BASE, 'dist-server/server/claude-sdk.js')
patch(sdk, [
    ("[Images provided at the following paths:]",
     "[Attached files provided at the following paths:]", None),
])

# ---- frontend bundle ----
bundle = glob.glob(os.path.join(BASE, 'dist/assets/index-*.js'))[0]
patch(bundle, [
    ('!$e.type||!$e.type.startsWith("image/")', '!1', 1),
    ('accept:{"image/*":[".png",".jpg",".jpeg",".gif",".webp",".svg"]},', '', 1),
    ('$e.size>5242880', '$e.size>26214400', None),
    ('maxSize:5*1024*1024', 'maxSize:25*1024*1024', None),
    ('File too large (max 5MB)', 'File too large (max 25MB)', None),
    ('maxFiles:5,onDrop:tt', 'maxFiles:20,onDrop:tt', 1),
    ('[...$e,...We].slice(0,5)', '[...$e,...We].slice(0,20)', 1),
    # attach button: relabel "Attach images"->"Attach files" + swap image icon (Vn) for file icon (qt)
    ('content:ie("input.attachImages")},onClick:H,children:i.jsx(Vn,{})',
     'content:ie("input.attachFiles")},onClick:H,children:i.jsx(qt,{})', 1),
    # default UI theme -> light (was: follow OS preference)
    ('getItem("theme")||r(l.matches)', 'getItem("theme")||"light"', 1),
    # (b) remove top tabs: Shell (terminal), Git (source control), Browser.
    # Ime is the builtin tab list [chat, shell, files, git]; the tab bar is
    # [...Ime, ...browser?, ...tasks?]. Drop shell + git from Ime and the browser spread.
    ('{kind:"builtin",id:"shell",labelKey:"tabs.shell",icon:Tr},', '', 1),
    (',{kind:"builtin",id:"git",labelKey:"tabs.git",icon:Ur}', '', 1),
    ('...n?[jme]:[],', '', 1),
    # (c) never report an update as available. In component aB the whole
    # version/"Update available" footer block is gated on `e` (=updateAvailable):
    # `e&&<block>`. Forcing the version-check hook to updateAvailable:!1 makes that
    # block render nothing (no version line, no update badge, no modal trigger) while
    # KEEPING Settings / Report Issue / Join Community — which live in the same aB but
    # are NOT gated on `e`. Also kills the collapsed mini-bar dot and the Settings page
    # mention. (Do NOT null aB itself — that also removes the footer nav items and breaks
    # the cc-ui/cc-design injections that anchor on the Report Issue link.)
    ('},[e,t]),{updateAvailable:r,latestVersion:a,currentVersion:Tu',
     '},[e,t]),{updateAvailable:!1,latestVersion:a,currentVersion:Tu', 1),
])

# ---- cache-bust: rename the JS bundle to a content hash and fix index.html refs ----
# The bundle is served immutable + cached by a service worker, so an in-place edit is
# NOT picked up unless the filename changes. Derive the name from the patched content
# so this is idempotent (unchanged content -> same name -> no rename).
idx = os.path.join(BASE, 'dist/index.html')
cur = glob.glob(os.path.join(BASE, 'dist/assets/index-*.js'))[0]
old_base = os.path.basename(cur)
h = hashlib.md5(open(cur, 'rb').read()).hexdigest()[:10]
new_base = f'index-{h}.js'
if old_base != new_base:
    os.rename(cur, os.path.join(os.path.dirname(cur), new_base))
    html = open(idx, encoding='utf-8').read()
    n = html.count(old_base)
    html = html.replace(old_base, new_base)
    open(idx, 'w', encoding='utf-8').write(html)
    report.append(f"  cache-bust: {old_base} -> {new_base} ({n} refs in index.html)")
else:
    report.append(f"  cache-bust: bundle name already current ({new_base})")

print("\n".join(report))
print("PATCH DONE")
