/* =============================================================================
   FusionMob — Cabinet Layout: the APP SHELL
   -----------------------------------------------------------------------------
   State, wiring and the conversation with the add-in. This is the file that
   loads LAST, because it is the only one with top-level executable code
   (FMCFG.initTooltips() plus the DOMContentLoaded boot at the bottom).

   Holds `state` (the one object every other page script reads) and `CTX` (the
   single mutable context the shared form closes over — refresh its lists in
   place, never replace it). Owns: the configuration form rendered from the
   shared SPEC, the property search mount, every event binding, the target
   selector, validate/apply, and this page's own help tooltips.

   Load order (see layout_editor.html): shared/palette_bridge,
   shared/cabinet_config, layout/model, layout/canvas, layout/wizard,
   layout/demo, then this file.
============================================================================= */
"use strict";

var state = { cfg:null, cabinets:[], materials:[], slides:[], fita_choices:[],
              target:"new", sel:null, demo:false };

/* Configuration sections rendered as collapsible cards below the interior
   editor (the "dims" section is rendered into the Medidas card instead). The
   advanced gate hides exactly these. */
var CFG_SECTIONS = ["fita","back","kick","joinery","tamponamento","arremate",
                    "puxador","doors","drawers","hinge","tol"];

/* Context handed to the shared form: this palette edits ONE cabinet, so it gets
   surface "cabinet" (the flat single-region fields — n_shelves, n_doors,
   n_drawers, inset — are per-region here and live in the Interior editor).
   One mutable object, because the shared wiring closes over it. */
var CTX = {
  surface: "cabinet",
  materials: [], slides: [], fitaChoices: [],
  onChange: function(){ readCfgFromForm(); render(); },
  onInput: function(id){
    readCfgFromForm();
    if (id==="W"||id==="H"||id==="D"||id==="t"){ renderDiagram(); highlightDim(id, true); }
  }
};
function syncCtx(){
  CTX.materials = state.materials || [];
  CTX.slides = state.slides || [];
  CTX.fitaChoices = state.fita_choices || [];
}


/* ============================================================================
   FORM  <->  CFG  — delegated to the shared spec, so this palette can never
   drift from Preferências. Nothing here enumerates fields.
============================================================================ */
function syncFormFromCfg(){ FMCFG.writeForm(state.cfg, CTX); }
function readCfgFromForm(){ FMCFG.readForm(state.cfg, CTX); }

/* ---- small DOM helpers ------------------------------------------------------ */
function setDisabled(id,d){ var el=byId(id); if(el) el.disabled=!!d; }
function show(id,v){ var el=byId(id); if(el) el.style.display = v?"flex":"none"; }
function fillSelect(id, items, cur){
  var el=byId(id); if(!el) return;
  el.innerHTML = items.map(function(x){
    return '<option'+(x===cur?' selected':'')+'>'+esc(x)+'</option>'; }).join("");
}
function fillTargets(){
  var el=byId("target");
  var opts = '<option value="new">+ Novo armário</option>';
  state.cabinets.forEach(function(cb){
    opts += '<option value="'+esc(cb.id)+'"'+(cb.id===state.target?' selected':'')+'>'+esc(cb.name)+'</option>';
  });
  el.innerHTML = opts; el.value = state.target;
}
function setStatus(msg, cls){
  var el=byId("status"); el.className = cls||"";
  byId("statusMsg").textContent = msg || "";
}

/* ---- validation (debounced) ------------------------------------------------- */
var vTimer=null;
function validateNow(){
  clearTimeout(vTimer);
  vTimer = setTimeout(function(){
    readCfgFromForm();
    if (state.demo){ setStatus("Modo demonstração — a validação roda no Fusion.", "warn"); return; }
    send("validate", {cfg:state.cfg}).then(function(r){
      if (!r) return;
      if (r.ok) setStatus("Pronto para gerar.", "ok");
      else setStatus(r.error||"Configuração inválida.", "err");
    });
  }, 220);
}

/* ============================================================================
   WIRING
============================================================================ */
/* Build every configuration row from the shared spec: the "dims" rows go inside
   the Medidas card (which draws its own live explainer, hence noDiagram), the
   rest become collapsible sections below the interior editor. */
function renderCfgForm(){
  syncCtx();
  FMCFG.render(byId("dimsFields"),
               { surface:"cabinet", sections:["dims"], plain:true, noDiagram:true });
  FMCFG.render(byId("cfgSections"), { surface:"cabinet", sections:CFG_SECTIONS });
  FMCFG.wire(CTX);          // one listener set; CTX covers every section
  FMCFG.searchApply();      // the fresh DOM has to re-honour an active search
}

/* Busca de propriedades. The bar and every bit of filtering live in the shared
   module; this only says which surface/sections it filters and hides the page's
   OWN chrome (the interior canvas, the Medidas card) while a query is active. */
function mountSearch(){
  FMCFG.searchMount({
    host: byId("searchHost"),
    surface: "cabinet",
    sections: ["dims"].concat(CFG_SECTIONS),
    // "dims" is rendered plain into the Medidas card, so it has no .section
    // wrapper -- name the host so its shared content gets scaffolded too
    plainHosts: { dims: "dimsFields" },
    onFilter: function(r){
      // the card itself is what has to go when nothing inside it matches
      var whole = !!r.secHit["dims"];
      var any = whole || (r.sections["dims"] || 0) > 0;
      byId("cardMedidas").classList.toggle("fmh-hide", r.filtering && !any);
      // the card draws its own explainer + closing hint: keep them only when the
      // whole section matched, matching how a real section behaves
      byId("dimsDiagram").classList.toggle("fmh-hide", r.filtering && !whole);
      byId("dimsHint").classList.toggle("fmh-hide", r.filtering && !whole);
      // not properties: pure noise in a result list
      byId("cardInterior").classList.toggle("fmh-hide", r.filtering);
      byId("advRow").classList.toggle("fmh-hide", r.filtering);
      byId("advHint").classList.toggle("fmh-hide", r.filtering);
    }
  });
}

function bindForm(){
  // live highlight of the diagram while a dimension has focus (the shared form
  // already handles the value -> cfg -> redraw path via CTX.onInput)
  ["W","H","D","t"].forEach(function(k){
    var el=byId(k); if(!el) return;
    el.addEventListener("focus", function(){ highlightDim(k,true); });
    el.addEventListener("blur",  function(){ highlightDim(k,false); });
  });

  // region type tiles
  document.querySelectorAll("#typeTiles .tile").forEach(function(t){
    t.addEventListener("click", function(){
      if (this.disabled || state.sel===null) return;
      var n=getNode(state.sel); if(isSplit(n)) return;
      n.type=this.getAttribute("data-type");
      if (n.type!=="open" && n.type!=="blind" && !n.count) n.count=(n.type==="shelves"?3:2);
      render();
    });
  });

  // count stepper
  byId("countUp").addEventListener("click", function(){ bumpCount(1); });
  byId("countDown").addEventListener("click", function(){ bumpCount(-1); });
  byId("leafCount").addEventListener("change", function(){
    var n=getNode(state.sel); n.count=Math.max(1, parseInt(this.value)||1); render(); });
  byId("sbUp").addEventListener("click", function(){ bumpSB(1); });
  byId("sbDown").addEventListener("click", function(){ bumpSB(-1); });
  byId("leafShelvesBehind").addEventListener("change", function(){
    var n=getNode(state.sel); n.shelves_behind=Math.max(0, parseInt(this.value)||0); render(); });

  byId("leafInset").addEventListener("change", function(){ getNode(state.sel).inset=this.checked; render(); });
  byId("leafAlign").addEventListener("change", function(){ getNode(state.sel).shelf_align_front=this.checked; render(); });
  byId("leafSize").addEventListener("change", function(){
    var w=getWrapper(state.sel); if(w){ w.size=Math.max(0.001, parseFloat(this.value)||1); render(); } });
  byId("leafFixed").addEventListener("change", function(){
    var w=getWrapper(state.sel); if(w){ w.fixed=this.checked; render(); } });

  byId("splitV").addEventListener("click", function(){ if(state.sel!==null){ splitLeaf(state.sel,"v"); render(); } });
  byId("splitH").addEventListener("click", function(){ if(state.sel!==null){ splitLeaf(state.sel,"h"); render(); } });
  byId("del").addEventListener("click", function(){ if(state.sel){ deleteRegion(state.sel); render(); } });

  // Advanced gate: keep the screen clean by hiding the detail sections (their
  // defaults come from Preferences) until "Configuração avançada" is ticked.
  // Hiding is cosmetic only — the shared readForm reads every field regardless.
  // The shared module is the single owner of section visibility, so it composes
  // this gate with the search filter (a match can surface a gated section)
  // instead of the two of them fighting over inline styles.
  function applyAdvanced(){
    FMCFG.setGatedSections(byId("advancedMode").checked ? [] : CFG_SECTIONS);
  }
  byId("advancedMode").addEventListener("change", applyAdvanced);
  applyAdvanced();

  // target selector
  byId("target").addEventListener("change", function(){
    var id=this.value; state.target=id;
    if (state.demo){ return; }
    send("selectTarget",{id:id}).then(function(r){
      if (r && r.cfg){ state.cfg=r.cfg; state.sel=null; render(); }
    });
  });

  byId("reload").addEventListener("click", function(){ init(0); });
  byId("apply").addEventListener("click", doApply);

  // wizard
  byId("openWizard").addEventListener("click", openWizard);
  byId("wizCancel").addEventListener("click", closeWizard);
  byId("wizBack").addEventListener("click", function(){ if(WIZ.step>0){ WIZ.step--; renderWizard(); } });
  byId("wizNext").addEventListener("click", wizAdvance);
}
function bumpCount(d){
  var n=getNode(state.sel); if(!n||isSplit(n)||n.type==="open") return;
  n.count=Math.max(1,(n.count||1)+d); render();
}
function bumpSB(d){
  var n=getNode(state.sel); if(!n||isSplit(n)||n.type!=="doors") return;
  n.shelves_behind=Math.max(0,(n.shelves_behind||0)+d); render();
}

function doApply(){
  readCfgFromForm();
  if (state.demo){
    setStatus("Modo demonstração — conecte pelo comando Cabinet Layout no Fusion para gerar.", "warn");
    return;
  }
  setStatus("Gerando…", "");
  send("apply", {id:state.target, cfg:state.cfg}).then(function(r){
    if (!r){ setStatus("Sem conexão com o Fusion.", "err"); return; }
    if (r.__err){ setStatus("Erro na ponte JS: "+r.__err, "err"); return; }
    if (!r.ok){ setStatus(r.error||"Falha ao gerar.", "err"); return; }
    state.cabinets = r.cabinets||state.cabinets;
    if (r.id) state.target = r.id;
    fillTargets();
    var w = (r.warnings&&r.warnings.length) ? " ("+r.warnings.length+" aviso(s))" : "";
    setStatus("Armário gerado."+w, "ok");
  });
}

function init(tries){
  tries = tries || 0;
  send("init", {}).then(function(r){
    if (!r){                                   // adsk bridge not ready yet — retry, then demo
      if (tries < 16){ setTimeout(function(){ init(tries+1); }, 250); return; }
      enterDemo(); return;
    }
    if (r.__err){ setStatus("Erro na ponte JS: " + r.__err, "err"); return; }
    if (r.__raw){ setStatus("Resposta inválida do add-in: " + r.__raw.slice(0,300), "err"); return; }
    if (!r.cfg){ setStatus(r.error || "O add-in não retornou uma configuração.", "err"); return; }
    state.demo = false; byId("demoBanner").classList.remove("show");
    state.materials = r.materials || [];
    state.slides = r.slides || [];
    state.fita_choices = r.fita_choices || [];
    state.cabinets = r.cabinets || [];
    state.cfg = r.cfg; state.target = "new"; state.sel = null;
    syncCtx(); fillTargets(); render();
    setStatus("Pronto. Edite o layout e clique Aplicar.", "ok");
  });
}

/* ---- inline help for this page's own controls (the configuration rows get
       theirs from the shared spec) ------------------------------------------ */
var HELP_DIRECT = {
  tileOpen:"Região vazia (nicho aberto), sem prateleiras/portas/gavetas.",
  tileShelves:"Prateleiras horizontais distribuídas igualmente na região.",
  tileDoors:"Portas de abrir cobrindo a região (furação de dobradiça opcional).",
  tileDrawers:"Coluna de gavetas empilhadas com corrediça na região.",
  tileBlind:"Painel cego fixo: bloqueia o acesso à região (ex.: canto morto de um armário em L). Sem abertura.",
  splitV:"Divide a região em linhas (uma sobre a outra), com divisória horizontal entre elas.",
  splitH:"Divide a região em colunas (lado a lado), com divisória vertical entre elas.",
  del:"Remove a região e junta o espaço com a vizinha.",
  leafCount:"Quantas prateleiras / portas / gavetas nesta região.",
  leafShelvesBehind:"Prateleiras dentro da região de portas (atrás das portas).",
  leafInset:"Ligado (embutida): frente assenta dentro do vão, faceada. Desligado (sobreposta): cobre a frente do corpo.",
  leafAlign:"Ligado: prateleiras alinhadas com a frente do corpo. Desligado: recuadas.",
  leafSize:"Tamanho da região dentro do pai. Como peso (fração do espaço livre) ou, ligando “fixo”, em mm exatos.",
  advancedMode:"Mostra as seções de detalhe (fundo, rodapé, frentes, fita, avançado). Desligado, elas usam os padrões das Preferências."
};
function injectHelp(){
  Object.keys(HELP_DIRECT).forEach(function(id){ FMCFG.setTip(id, HELP_DIRECT[id]); });
  // the per-region rows carry their tip on the label, like the shared rows do
  ["leafCount","leafShelvesBehind","leafInset","leafAlign","leafSize"].forEach(function(id){
    var el = byId(id); if(!el) return;
    var row = el.closest(".row"); if(!row) return;
    var lab = row.querySelector("label"); if(!lab || lab.querySelector(".info")) return;
    lab.appendChild(FMCFG.makeInfo(HELP_DIRECT[id]));
  });
}

FMCFG.initTooltips();
document.addEventListener("DOMContentLoaded", function(){
  mountSearch(); renderCfgForm(); bindForm(); injectHelp(); init(0);
});
