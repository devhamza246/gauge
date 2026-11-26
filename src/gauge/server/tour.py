"""Guided tour for Streamlit apps: dims the page, highlights real widgets one by one and explains each in a popup.

    from tour import run_tour
    run_tour("askbase", [{"sel": '[data-testid="stSidebar"]', "title": "...", "body": "..."}, ...])

A step targets an element by CSS selector `sel`, optionally narrowed with `text` (the first matching element whose text contains it).
A step with no match is shown centred, so a missing widget never breaks the tour. It starts by itself on a visitor's first visit
(remembered in the browser's localStorage) and again whenever st.session_state["tour_nonce"] changes (the "Take the tour" button).
Nothing is sent anywhere: it is plain DOM code running in the visitor's browser.
"""
from __future__ import annotations

import json

import streamlit as st
import streamlit.components.v1 as components

_JS = r"""
<script>
(function () {
  const cfg = __CFG__;
  let P; try { P = window.parent; P.document.body; } catch (e) { return; }
  const D = P.document;
  const seenKey = "tour-seen-" + cfg.id;
  let seen = false; try { seen = !!P.localStorage.getItem(seenKey); } catch (e) {}
  const nonceKey = "__tourNonce_" + cfg.id;
  const manual = cfg.nonce && P[nonceKey] !== cfg.nonce;
  P[nonceKey] = cfg.nonce;
  if (!manual && (seen || D.getElementById("tour-root"))) return;
  if (D.getElementById("tour-root")) D.getElementById("tour-root").remove();

  const css = D.createElement("style"); css.id = "tour-css";
  css.textContent = `
  #tour-root{position:fixed;inset:0;z-index:2147483000;pointer-events:none;font-family:system-ui,-apple-system,Segoe UI,sans-serif}
  #tour-block{position:fixed;inset:0;pointer-events:auto}
  #tour-hole{position:fixed;border-radius:10px;box-shadow:0 0 0 9999px rgba(10,14,25,.62),0 0 0 3px #6ee7b7;transition:all .25s ease;pointer-events:none}
  #tour-card{position:fixed;width:min(340px,calc(100vw - 24px));background:#fff;color:#111827;border-radius:14px;padding:16px 16px 12px;
    box-shadow:0 12px 40px rgba(0,0,0,.35);pointer-events:auto;line-height:1.45}
  #tour-card h4{margin:0 0 6px;font-size:16px}
  #tour-card p{margin:0 0 12px;font-size:14px;color:#374151}
  #tour-bar{display:flex;align-items:center;gap:8px}
  #tour-bar .n{flex:1;font-size:12px;color:#6b7280}
  #tour-card button{font:inherit;font-size:13px;border-radius:8px;padding:6px 12px;border:1px solid #d1d5db;background:#fff;color:#111827;cursor:pointer}
  #tour-card button.p{background:#059669;border-color:#059669;color:#fff}
  #tour-card button:focus-visible{outline:3px solid #2563eb;outline-offset:2px}
  #tour-card button.s{border:none;background:none;color:#6b7280;text-decoration:underline;padding:6px 4px}
  @media (prefers-reduced-motion:reduce){#tour-hole{transition:none}}`;
  D.head.appendChild(css);

  const root = D.createElement("div"); root.id = "tour-root";
  root.innerHTML = '<div id="tour-block"></div><div id="tour-hole"></div>' +
    '<div id="tour-card" role="dialog" aria-modal="true" aria-live="polite"><h4></h4><p></p><div id="tour-bar">' +
    '<span class="n"></span><button class="s" type="button">Skip</button><button class="b" type="button">Back</button>' +
    '<button class="p" type="button">Next</button></div></div>';
  D.body.appendChild(root);
  const hole = root.querySelector("#tour-hole"), card = root.querySelector("#tour-card");
  const h4 = card.querySelector("h4"), para = card.querySelector("p"), num = card.querySelector(".n");
  const bSkip = card.querySelector(".s"), bBack = card.querySelector(".b"), bNext = card.querySelector(".p");
  let i = 0;

  function find(step) {
    if (!step.sel) return null;
    const all = Array.from(D.querySelectorAll(step.sel));
    const hit = step.text ? all.find(e => (e.textContent || "").toLowerCase().includes(step.text.toLowerCase())) : all[0];
    return hit || null;
  }
  function finish() {
    try { P.localStorage.setItem(seenKey, "1"); } catch (e) {}
    root.remove(); css.remove(); D.removeEventListener("keydown", onKey, true);
  }
  function onKey(ev) {
    if (ev.key === "Escape") finish();
    else if (ev.key === "ArrowRight" || ev.key === "Enter") { ev.preventDefault(); go(1); }
    else if (ev.key === "ArrowLeft") { ev.preventDefault(); go(-1); }
  }
  function show() {
    const step = cfg.steps[i], el = find(step);
    h4.textContent = step.title; para.textContent = step.body;
    num.textContent = "Step " + (i + 1) + " of " + cfg.steps.length;
    bBack.style.visibility = i === 0 ? "hidden" : "visible";
    bNext.textContent = i === cfg.steps.length - 1 ? "Done" : "Next";
    const vw = P.innerWidth, vh = P.innerHeight, cw = card.offsetWidth || 340, ch = card.offsetHeight || 160;
    root.querySelector("#tour-block").style.background = "transparent";
    if (el) {
      el.scrollIntoView({block: "center", behavior: "auto"});
      const r = el.getBoundingClientRect(), pad = 6;
      const top = Math.max(4, r.top - pad), left = Math.max(4, r.left - pad);
      hole.style.display = "block";
      hole.style.top = top + "px"; hole.style.left = left + "px";
      hole.style.width = Math.min(vw - left - 4, r.width + pad * 2) + "px";
      hole.style.height = Math.min(vh - top - 4, r.height + pad * 2) + "px";
      let cy = r.bottom + 14; if (cy + ch > vh - 8) cy = Math.max(8, r.top - ch - 14);
      if (cy < 8 || cy + ch > vh - 8) cy = Math.max(8, vh - ch - 12);
      let cx = Math.min(Math.max(12, r.left), vw - cw - 12);
      if (r.width < vw * 0.4 && r.right + cw + 24 < vw) {  // a narrow target (e.g. in the sidebar): put the card beside it
        cx = r.right + 20; cy = Math.min(Math.max(8, r.top + r.height / 2 - ch / 2), vh - ch - 8);
      }
      card.style.top = cy + "px"; card.style.left = cx + "px";
    } else {
      hole.style.display = "none";
      root.querySelector("#tour-block").style.background = "rgba(10,14,25,.62)";
      card.style.top = Math.max(12, (vh - ch) / 2) + "px"; card.style.left = Math.max(12, (vw - cw) / 2) + "px";
    }
    bNext.focus();
  }
  function go(d) { const n = i + d; if (n < 0) return; if (n >= cfg.steps.length) return finish(); i = n; show(); }
  bSkip.onclick = finish; bBack.onclick = () => go(-1); bNext.onclick = () => go(1);
  D.addEventListener("keydown", onKey, true);
  P.addEventListener("resize", () => { if (D.getElementById("tour-root")) show(); });
  setTimeout(show, cfg.delay || 700);
})();
</script>
"""


def run_tour(tour_id: str, steps: list[dict], delay_ms: int = 700) -> None:
    """Inject the tour. Call once per script run, after the widgets it points at have been drawn."""
    cfg = {"id": tour_id, "steps": steps, "nonce": st.session_state.get("tour_nonce", 0), "delay": delay_ms}
    components.html(_JS.replace("__CFG__", json.dumps(cfg)), height=0)


def tour_button(label: str = "Take the tour") -> None:
    """A sidebar/page button that restarts the tour."""
    if st.button(label, use_container_width=True, key="tour_btn"):
        st.session_state["tour_nonce"] = st.session_state.get("tour_nonce", 0) + 1
        st.rerun()
