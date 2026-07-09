#!/usr/bin/env bash
# Inject into the panel's dist/index.html, bundle-independent (survives `cloudcli update`
# and bundle rebuilds, served no-store, cannot blank the SPA). Idempotent. Sections:
#   1) Inter UI font + html/body font-family override.
#   2) cc-ui: rebrand "CloudCLI" -> $BRAND, hide GitHub star/version badges and the
#      "open source" text, add a "Выйти" (logout) item to the footer menu.
#   3) cc-design: a native "Claude Design" launcher button (only if $DESIGN_URL is set).
#   4) cc-theme: terracotta Claude accent (blue -> clay #c96442), read from a CSS file.
#   5) cc-nav: a visible "expand sidebar" chevron for the collapsed sidebar.
#
# Configuration (env vars, with safe placeholder defaults):
#   BRAND       panel brand shown in the UI            (default: ClaudeCode)
#   DESIGN_URL  full Claude Design URL incl. access key (default: empty -> button skipped)
#               e.g. https://design.<host>/?k=<secret>
#
# Usage: BRAND=ClaudeCode DESIGN_URL='https://design.<host>/?k=<secret>' \
#        bash inject-font.sh [INSTALL_DIR] [WOFF2_PATH] [THEME_CSS_PATH]
set -e
BASE="${1:-/usr/lib/node_modules/@cloudcli-ai/cloudcli}"
SRC="${2:-/root/InterVariable.woff2}"
THEME="${3:-/root/cc-theme.css}"
BRAND="${BRAND:-ClaudeCode}"
DESIGN_URL="${DESIGN_URL:-}"
D="$BASE/dist"

mkdir -p "$D/assets/fonts"
cp "$SRC" "$D/assets/fonts/InterVariable.woff2"

# 1) Font
if grep -q 'id="cc-font"' "$D/index.html"; then
  echo "font override already present"
else
  STYLE='<style id="cc-font">@font-face{font-family:"InterVar";font-style:normal;font-weight:100 900;font-display:swap;src:url("/assets/fonts/InterVariable.woff2") format("woff2")}html,body{font-family:"InterVar",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif !important}</style>'
  python3 - "$D/index.html" "$STYLE" <<'PY'
import sys
p, style = sys.argv[1], sys.argv[2]
s = open(p, encoding='utf-8').read()
if '</head>' in s:
    s = s.replace('</head>', style + '</head>', 1)
    open(p, 'w', encoding='utf-8').write(s)
    print("font override injected")
else:
    print("WARN: </head> not found")
PY
fi

# 2) UI script: rebrand + hide star/open-source + logout-in-menu (bundle-independent)
# Remove any earlier floating-logout script first.
python3 - "$D/index.html" <<'PY'
import re, sys
p = sys.argv[1]
s = open(p, encoding='utf-8').read()
s = re.sub(r'<script id="cc-logout-btn">.*?</script>', '', s, flags=re.S)
open(p, 'w', encoding='utf-8').write(s)
PY

if grep -q 'id="cc-ui"' "$D/index.html"; then
  echo "cc-ui already present"
else
  # __BRAND__ is replaced with $BRAND below (kept out of the single-quoted JS so the
  # brand string cannot break bash/JS quoting).
  UI='<script id="cc-ui">(function(){function logout(){try{fetch("/api/auth/logout",{method:"POST",headers:{Authorization:"Bearer "+localStorage.getItem("auth-token")}})}catch(e){}localStorage.removeItem("auth-token");location.reload();}function apply(){if(!document.body)return;var i,el,all=document.querySelectorAll("span,h1,h2,p,a,strong,div,button");for(i=0;i<all.length;i++){el=all[i];if(el.childElementCount===0&&el.textContent){if(el.textContent.indexOf("CloudCLI")>=0){el.textContent=el.textContent.replace(/CloudCLI/g,"__BRAND__");}var lt=el.textContent.toLowerCase();if(lt.indexOf("open source")>=0||lt.indexOf("open-source")>=0){var oc=el.closest("a");(oc||el).style.display="none";}}}var la=document.getElementsByTagName("a"),reportRef=null;for(i=0;i<la.length;i++){var h=la[i].href||"";if(h.indexOf("github.com/siteboon/claudecodeui")>=0){if(h.indexOf("/issues")>=0){reportRef=la[i];}else{la[i].style.display="none";}}}if(localStorage.getItem("auth-token")&&reportRef&&reportRef.parentElement&&!document.getElementById("ccLogout")){var host=reportRef.parentElement,menu=host.parentElement||host;var w=document.createElement(host.tagName);w.className=host.className;w.id="ccLogout";var b=document.createElement("button");b.type="button";b.className=reportRef.className;b.textContent="Выйти";b.style.width="100%";b.onclick=logout;w.appendChild(b);menu.appendChild(w);}}setInterval(apply,900);if(document.readyState!=="loading"){apply();}else{document.addEventListener("DOMContentLoaded",apply);}})();</script>'
  UI="${UI//__BRAND__/$BRAND}"
  python3 - "$D/index.html" "$UI" <<'PY'
import sys
p, snip = sys.argv[1], sys.argv[2]
s = open(p, encoding='utf-8').read()
if '</body>' in s:
    s = s.replace('</body>', snip + '</body>', 1)
elif '</head>' in s:
    s = s.replace('</head>', snip + '</head>', 1)
open(p, 'w', encoding='utf-8').write(s)
print("cc-ui injected")
PY
fi

# 3) Claude Design launcher button (bundle-independent, idempotent).
# Only injected when DESIGN_URL is set. Once logged in, clones the "New Project"
# sidebar button style and inserts a sibling "Claude Design" button opening
# open-design in a new tab; falls back to the footer settings menu. JS has NO single
# quotes and NO nested double-quoted HTML attributes (SVG built via DOM), so bash
# single-quote wrapping is safe. Re-run strips the old block first (URL can change).
python3 - "$D/index.html" <<'PY'
import re, sys
p = sys.argv[1]
s = open(p, encoding='utf-8').read()
s = re.sub(r'<script id="cc-design">.*?</script>', '', s, flags=re.S)
open(p, 'w', encoding='utf-8').write(s)
PY
if [ -n "$DESIGN_URL" ]; then
  # __DESIGN_URL__ replaced with $DESIGN_URL below.
  DESIGN='<script id="cc-design">(function(){var U="__DESIGN_URL__",NS="http://www.w3.org/2000/svg";function od(){window.open(U,"_blank","noopener");}function icon(){var svg=document.createElementNS(NS,"svg");svg.setAttribute("width","16");svg.setAttribute("height","16");svg.setAttribute("viewBox","0 0 24 24");svg.setAttribute("fill","none");svg.setAttribute("stroke","currentColor");svg.setAttribute("stroke-width","2");svg.setAttribute("stroke-linecap","round");svg.setAttribute("stroke-linejoin","round");svg.style.flex="0 0 auto";var p1=document.createElementNS(NS,"path");p1.setAttribute("d","M12 20h9");svg.appendChild(p1);var p2=document.createElementNS(NS,"path");p2.setAttribute("d","M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z");svg.appendChild(p2);return svg;}function mk(cls){var b=document.createElement("button");b.type="button";b.id="ccDesign";if(cls){b.className=cls;}b.title="Claude Design";b.style.display="flex";b.style.alignItems="center";b.style.gap="8px";var tx=document.createElement("span");tx.textContent="Claude Design";b.appendChild(icon());b.appendChild(tx);b.onclick=od;return b;}function npBtn(){var e=document.querySelectorAll("button,a"),i,t;for(i=0;i<e.length;i++){t=(e[i].textContent||"").trim();if(t==="New Project"||t==="New project"){return e[i];}}return null;}function apply(){if(!document.body){return;}if(!localStorage.getItem("auth-token")){return;}if(document.getElementById("ccDesign")){return;}var a=npBtn();if(a&&a.parentElement){a.parentElement.insertBefore(mk(a.className),a.nextSibling);return;}var la=document.getElementsByTagName("a"),j,h;for(j=0;j<la.length;j++){h=la[j].href||"";if(h.indexOf("github.com/siteboon/claudecodeui")>=0&&h.indexOf("/issues")>=0){var host=la[j].parentElement,menu=host&&host.parentElement;if(menu){var w=document.createElement(host.tagName);w.className=host.className;w.appendChild(mk(la[j].className));menu.insertBefore(w,menu.firstChild);return;}}}}setInterval(apply,900);if(document.readyState!=="loading"){apply();}else{document.addEventListener("DOMContentLoaded",apply);}})();</script>'
  DESIGN="${DESIGN//__DESIGN_URL__/$DESIGN_URL}"
  python3 - "$D/index.html" "$DESIGN" <<'PY'
import sys
p, snip = sys.argv[1], sys.argv[2]
s = open(p, encoding='utf-8').read()
if '</body>' in s:
    s = s.replace('</body>', snip + '</body>', 1)
elif '</head>' in s:
    s = s.replace('</head>', snip + '</head>', 1)
open(p, 'w', encoding='utf-8').write(s)
print("cc-design injected")
PY
else
  echo "cc-design skipped (DESIGN_URL empty)"
fi

# 4) Terracotta theme (Claude look): recolor the panel's shadcn default-BLUE accent
# to Claude clay (#c96442). Two layers in one <style id="cc-theme">: token overrides
# (--primary/--ring/nav focus vars, light + dark) and !important overrides for the
# hardcoded blue-* utility classes and literal blue hexes that bypass the token.
# Read from a CSS file (not a bash var) so single quotes in the CSS can't break
# wrapping. Injected before </head> (after the bundle <link>, so !important wins),
# served no-store => survives bundle rebuilds and `cloudcli update`.
if grep -q 'id="cc-theme"' "$D/index.html"; then
  echo "cc-theme already present"
elif [ -f "$THEME" ]; then
  python3 - "$D/index.html" "$THEME" <<'PY'
import sys
p, cssp = sys.argv[1], sys.argv[2]
css = open(cssp, encoding='utf-8').read()
block = '<style id="cc-theme">' + css + '</style>'
s = open(p, encoding='utf-8').read()
if '</head>' in s:
    s = s.replace('</head>', block + '</head>', 1)
    open(p, 'w', encoding='utf-8').write(s)
    print("cc-theme injected (%d css bytes)" % len(css))
else:
    print("WARN: </head> not found")
PY
else
  echo "WARN: cc-theme css not found at $THEME"
fi

# 5) Expand-sidebar button (bundle-independent, idempotent).
# When the left sidebar is collapsed, the panel's own "Show sidebar" button renders
# at the very bottom of a near-invisible 48px mini-bar (off-screen, faint grey icon) —
# users cannot find it and get stuck with no menu. This injects a clearly visible
# terracotta chevron at the top-left whenever the sidebar is collapsed; clicking it
# clicks the native (hidden) expand button. Removed automatically when expanded again.
# JS uses NO single quotes and builds its SVG via DOM, so bash single-quote wrapping holds.
# Strip any earlier cc-nav first so edits to this section take effect on re-run.
python3 - "$D/index.html" <<'PY'
import re, sys
p = sys.argv[1]
s = open(p, encoding='utf-8').read()
s = re.sub(r'<script id="cc-nav">.*?</script>', '', s, flags=re.S)
open(p, 'w', encoding='utf-8').write(s)
PY
NAV='<script id="cc-nav">(function(){var NS="http://www.w3.org/2000/svg";function findExpand(){var bs=document.querySelectorAll("button"),i,b,r,s;for(i=0;i<bs.length;i++){b=bs[i];r=b.getBoundingClientRect();if(r.width<=0||r.left>=90){continue;}s=((b.getAttribute("aria-label")||"")+" "+(b.getAttribute("title")||"")).toLowerCase();if(s.indexOf("show sidebar")>=0||s.indexOf("развернуть")>=0||s.indexOf("показать")>=0){return b;}}return null;}function icon(){var s=document.createElementNS(NS,"svg");s.setAttribute("width","18");s.setAttribute("height","18");s.setAttribute("viewBox","0 0 24 24");s.setAttribute("fill","none");s.setAttribute("stroke","currentColor");s.setAttribute("stroke-width","2");s.setAttribute("stroke-linecap","round");s.setAttribute("stroke-linejoin","round");var p=document.createElementNS(NS,"path");p.setAttribute("d","M9 18l6-6-6-6");s.appendChild(p);return s;}function mk(){var b=document.createElement("button");b.type="button";b.id="ccExpand";b.title="Развернуть меню";b.setAttribute("aria-label","Развернуть меню");b.style.cssText="position:fixed;top:10px;left:7px;z-index:9999;width:34px;height:34px;display:flex;align-items:center;justify-content:center;border:none;border-radius:9px;background:#c96442;color:#fff;cursor:pointer;box-shadow:0 1px 4px rgba(0,0,0,.25)";b.appendChild(icon());b.onclick=function(){var t=findExpand();if(t){t.click();}b.remove();};return b;}function apply(){if(!document.body){return;}var t=findExpand(),btn=document.getElementById("ccExpand");if(t){if(!btn){document.body.appendChild(mk());}}else if(btn){btn.remove();}}setInterval(apply,700);if(document.readyState!=="loading"){apply();}else{document.addEventListener("DOMContentLoaded",apply);}})();</script>'
python3 - "$D/index.html" "$NAV" <<'PY'
import sys
p, snip = sys.argv[1], sys.argv[2]
s = open(p, encoding='utf-8').read()
if '</body>' in s:
    s = s.replace('</body>', snip + '</body>', 1)
elif '</head>' in s:
    s = s.replace('</head>', snip + '</head>', 1)
open(p, 'w', encoding='utf-8').write(s)
print("cc-nav injected")
PY
