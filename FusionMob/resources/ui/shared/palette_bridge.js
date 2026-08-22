/* =============================================================================
   FusionMob — palette <-> add-in bridge (shared by BOTH palettes)
   -----------------------------------------------------------------------------
   The single wrapper around Fusion's `adsk.fusionSendData`, plus the two DOM
   one-liners every page needs. Loaded by layout_editor.html and
   preferences.html alike, so the request/response contract with Python is
   written once instead of copy-pasted per palette.

   Python answers by setting `HTMLEventArgs.returnData`; a reply is parsed JSON,
   or `{__raw}` when it isn't JSON, or `{__err}` when the bridge threw, or
   `null` when `adsk` isn't there yet (a plain browser, or Fusion still wiring
   the palette up — callers retry on `null`).

   No dependencies, no build step: plain <script src="shared/palette_bridge.js">.
============================================================================= */
"use strict";

/* ---- bridge to the add-in (unchanged contract) ------------------------------ */
function send(action, obj){
  try {
    if (typeof adsk !== "undefined" && adsk.fusionSendData){
      var ret = adsk.fusionSendData(action, JSON.stringify(obj||{}));
      return Promise.resolve(ret).then(function(s){
        if (s === undefined || s === null || s === "") return null;
        try { return JSON.parse(s); } catch(e){ return {__raw:String(s)}; }
      }, function(e){ return {__err:String(e)}; });
    }
  } catch(e){ return Promise.resolve({__err:String(e)}); }
  return Promise.resolve(null);   // adsk bridge not ready / running in a browser
}

/* ---- DOM one-liners both palettes use --------------------------------------- */
function byId(id){ return document.getElementById(id); }
function esc(s){ return String(s==null?"":s).replace(/[&<>"]/g,function(m){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]; }); }
