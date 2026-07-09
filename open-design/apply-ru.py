#!/usr/bin/env python3
# Apply Russian translations to open-design's ru.ts locale.
# For each key in the translations JSON, replace the value in the line
#   'key': 'old english'   ->   'key': 'русское'
# Single-line entries only (matches the extraction). Idempotent-ish (re-running
# with the same map re-writes the same values). Validates by re-parsing after.
# Usage: python3 apply-ru.py <translations.json> [ru.ts path]
import re, json, sys

TR = sys.argv[1] if len(sys.argv) > 1 else '/tmp/ru_translations.json'
RU = sys.argv[2] if len(sys.argv) > 2 else '/opt/open-design/apps/web/src/i18n/locales/ru.ts'

tr = json.load(open(TR, encoding='utf-8'))
s = open(RU, encoding='utf-8').read()


def esc(v):
    # For a single-quoted JS string. Translators were told to avoid ASCII
    # apostrophes; we still escape defensively and normalise newlines.
    v = v.replace("'", "\\'")
    v = v.replace('\r', '').replace('\n', '\\n')
    return v


applied = 0
missing = []
skipped_apostrophe = 0
for key, ru in tr.items():
    if not ru or not isinstance(ru, str):
        continue
    ru_esc = esc(ru)
    rx = re.compile(r"(^[ \t]*'" + re.escape(key) + r"':[ \t]*')(?:[^'\\]|\\.)*(')", re.M)

    def _r(m, val=ru_esc):
        return m.group(1) + val + m.group(2)

    s2, n = rx.subn(_r, s, count=1)
    if n == 1:
        s = s2
        applied += 1
    else:
        missing.append(key)

open(RU, 'w', encoding='utf-8').write(s)

# sanity: re-parse and count keys + remaining english leftovers
pat = re.compile(r"^\s*'([A-Za-z0-9_.]+)':\s*'((?:[^'\\]|\\.)*)'\s*,?\s*$", re.M)
pairs = pat.findall(s)
cyr = re.compile('[А-Яа-яЁё]')
lat = re.compile('[A-Za-z]')
leftover = sum(1 for k, v in pairs if lat.search(v) and not cyr.search(v))

print(f"applied: {applied}")
print(f"missing keys (not found): {len(missing)}")
if missing[:15]:
    print("  e.g.:", missing[:15])
print(f"parsed single-line keys after: {len(pairs)} (expect ~3947)")
print(f"english-leftover after: {leftover} (was 782)")
