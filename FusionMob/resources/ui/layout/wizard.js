/* =============================================================================
   FusionMob — Cabinet Layout: the setup WIZARD (Assistente)
   -----------------------------------------------------------------------------
   A 4-step overlay for people who would rather answer questions than fill a
   form: pick a cabinet preset, confirm the measures, pick an interior layout,
   review, build. Self-contained — it writes into `state.cfg` (including
   `state.cfg.layout`, built from the INTERIORS presets) and hands off to
   doApply() on the last step.

   Each step's buttons are bound inside renderWizard(), which re-runs per step.
============================================================================= */
"use strict";

/* ============================================================================
   WIZARD
============================================================================ */
var WIZ = { step:0, choicePreset:null, choiceInterior:null };
var WIZ_STEPS = 4;

var PRESETS = {
  base:   { name:"Balcão (base)",       W:800, H:850,  D:560, toe:true,  d:"Bancada de cozinha, apoio no chão." },
  wall:   { name:"Aéreo",               W:800, H:700,  D:350, toe:false, d:"Fixado na parede, acima da bancada." },
  tall:   { name:"Torre / Despenseiro", W:600, H:2100, D:600, toe:true,  d:"Coluna alta, do chão ao teto." },
  custom: { name:"Personalizado",       W:null,H:null, D:null, toe:null, d:"Começar do zero com as medidas atuais." }
};

function presetSVG(kind){
  // small illustrative silhouette per preset
  if (kind==="base")  return '<svg viewBox="0 0 120 70"><rect x="18" y="14" width="84" height="42" rx="2" fill="#3f6b4a" stroke="#6cc04a" stroke-width="1.5"/><path d="M60 14v42" stroke="#6cc04a" stroke-width="1.5"/><rect x="22" y="58" width="76" height="6" fill="#555"/></svg>';
  if (kind==="wall")  return '<svg viewBox="0 0 120 70"><rect x="24" y="8" width="72" height="36" rx="2" fill="#2f5d78" stroke="#29b6e8" stroke-width="1.5"/><path d="M60 8v36" stroke="#29b6e8" stroke-width="1.5"/></svg>';
  if (kind==="tall")  return '<svg viewBox="0 0 120 70"><rect x="42" y="4" width="36" height="60" rx="2" fill="#8a5a24" stroke="#e6a23c" stroke-width="1.5"/><path d="M42 24h36M42 40h36" stroke="#e6a23c" stroke-width="1.2"/></svg>';
  return '<svg viewBox="0 0 120 70"><rect x="24" y="10" width="72" height="48" rx="2" fill="#454545" stroke="#9a9a9a" stroke-width="1.5" stroke-dasharray="4 3"/><text x="60" y="38" text-anchor="middle" fill="#9a9a9a" font-size="10">?</text></svg>';
}

var INTERIORS = {
  doors2:    { name:"2 portas",              d:"Duas portas de abrir.",            build:function(){ return leafNode("doors",2); } },
  doorShelf: { name:"Portas + prateleiras",  d:"Portas com prateleiras dentro.",   build:function(){ var n=leafNode("doors",2); n.shelves_behind=3; return n; } },
  drawers3:  { name:"3 gavetas",             d:"Coluna de gavetas.",               build:function(){ return leafNode("drawers",3); } },
  shelves:   { name:"Prateleiras abertas",   d:"Sem portas, só prateleiras.",      build:function(){ return leafNode("shelves",3); } },
  combo:     { name:"Portas + nicho aberto", d:"Portas embaixo, nicho em cima.",   build:function(){
                 return { split:"v", children:[
                   { size:2, fixed:false, node:leafNode("doors",2) },   // bottom
                   { size:1, fixed:false, node:leafNode("shelves",2) } ]}; } },
  drawersDoors:{ name:"Gavetas + porta",     d:"Gavetas embaixo, porta em cima.",  build:function(){
                 return { split:"v", children:[
                   { size:1, fixed:false, node:leafNode("drawers",3) },
                   { size:1, fixed:false, node:leafNode("doors",1) } ]}; } }
};
function leafNode(type,count){ var n=newLeaf(type); n.count=count; return n; }

function interiorSVG(kind){
  var g = { doors2:'<rect x="8" y="6" width="104" height="58" fill="none" stroke="#2f6b4a" stroke-width="2"/><path d="M60 6v58" stroke="#2f6b4a" stroke-width="2"/><circle cx="54" cy="35" r="2.5" fill="#2f6b4a"/><circle cx="66" cy="35" r="2.5" fill="#2f6b4a"/>',
    doorShelf:'<rect x="8" y="6" width="104" height="58" fill="none" stroke="#2f6b4a" stroke-width="2"/><path d="M60 6v58M14 24h92M14 44h92" stroke="#2f6b4a" stroke-width="1.4"/>',
    drawers3:'<rect x="8" y="6" width="104" height="58" fill="none" stroke="#8a5a24" stroke-width="2"/><path d="M8 25h104M8 44h104" stroke="#8a5a24" stroke-width="1.6"/><path d="M50 15h20M50 34h20M50 54h20" stroke="#8a5a24" stroke-width="2"/>',
    shelves:'<rect x="8" y="6" width="104" height="58" fill="none" stroke="#2f5d78" stroke-width="2"/><path d="M8 22h104M8 35h104M8 48h104" stroke="#2f5d78" stroke-width="1.6"/>',
    combo:'<rect x="8" y="6" width="104" height="58" fill="none" stroke="#9a9a9a" stroke-width="1.5"/><path d="M8 24h104" stroke="#9a9a9a"/><path d="M60 24v40" stroke="#2f6b4a" stroke-width="2"/><path d="M14 13h92" stroke="#2f5d78" stroke-width="1.4"/>',
    drawersDoors:'<rect x="8" y="6" width="104" height="58" fill="none" stroke="#9a9a9a" stroke-width="1.5"/><path d="M8 30h104" stroke="#9a9a9a"/><path d="M8 42h104M8 54h104" stroke="#8a5a24" stroke-width="1.5"/>'
  }[kind]||"";
  return '<svg viewBox="0 0 120 70">'+g+'</svg>';
}

function openWizard(){
  WIZ.step = 0; WIZ.choicePreset = null; WIZ.choiceInterior = null;
  byId("wizard").classList.add("show");
  renderWizard();
}
function closeWizard(){ byId("wizard").classList.remove("show"); render(); }

function renderWizard(){
  var stepsEl = byId("wizSteps"); stepsEl.innerHTML = "";
  for (var i=0;i<WIZ_STEPS;i++){
    var cls = i<WIZ.step ? "done" : (i===WIZ.step ? "cur":"");
    stepsEl.innerHTML += '<span class="s '+cls+'"></span>';
  }
  var body = byId("wizBody"), title = byId("wizTitle");
  var back = byId("wizBack"), next = byId("wizNext");
  back.style.visibility = WIZ.step===0 ? "hidden":"visible";

  if (WIZ.step===0){
    title.textContent = "Que tipo de móvel?";
    body.innerHTML = '<p class="steplead">Escolha um ponto de partida. Você pode mudar tudo depois.</p>'+
      '<div class="choices">'+ Object.keys(PRESETS).map(function(k){
        var p=PRESETS[k];
        return '<button class="choice'+(WIZ.choicePreset===k?" on":"")+'" data-preset="'+k+'">'+
          presetSVG(k)+'<span class="cn">'+p.name+'</span><span class="cd">'+p.d+'</span></button>';
      }).join("")+'</div>';
    body.querySelectorAll("[data-preset]").forEach(function(b){
      b.addEventListener("click", function(){ pickPreset(this.getAttribute("data-preset")); });
    });
    next.disabled = !WIZ.choicePreset;
  }
  else if (WIZ.step===1){
    title.textContent = "Medidas";
    var c = state.cfg;
    body.innerHTML =
      '<p class="steplead">Confira as medidas externas. A altura inclui o rodapé.</p>'+
      '<div class="diagram"><svg viewBox="-24 0 286 210" id="wizDiagram"></svg></div>'+
      wizRow("wW","Largura (L)","mm",c.W)+ wizRow("wH","Altura (A)","mm",c.H)+
      wizRow("wD","Profundidade (P)","mm",c.D)+
      '<div class="row"><label>Material</label><select id="wMat" class="grow"></select></div>'+
      wizRow("wT","Espessura","mm",c.t)+
      '<div class="subhead">Fita de borda</div>'+
      '<p class="steplead">Espessura da fita nas bordas visíveis. Padrão: corpo 0,4mm, frentes 1mm.</p>'+
      wizFitaRow("wFitaCarcass","Bordas do corpo")+
      wizFitaRow("wFitaFronts","Frentes (portas/gavetas)");
    fillSelect("wMat", state.materials, c.material);
    var wf = c.fita||{};
    byId("wFitaCarcass").value = wf.carcass||"thin";
    byId("wFitaFronts").value = wf.fronts||"thick";
    // mirror the main diagram into the wizard
    var main = byId("dimDiagram"); if (main) byId("wizDiagram").innerHTML = main.innerHTML;
    ["wW","wH","wD","wT"].forEach(function(id){
      byId(id).addEventListener("input", wizReadMeasures);
    });
    byId("wMat").addEventListener("change", wizReadMeasures);
    byId("wFitaCarcass").addEventListener("change", wizReadMeasures);
    byId("wFitaFronts").addEventListener("change", wizReadMeasures);
    next.disabled = false;
  }
  else if (WIZ.step===2){
    title.textContent = "O que vai dentro?";
    body.innerHTML = '<p class="steplead">Escolha uma organização pronta. No editor você pode ' +
      'refinar região por região.</p><div class="choices">'+ Object.keys(INTERIORS).map(function(k){
        var it=INTERIORS[k];
        return '<button class="choice'+(WIZ.choiceInterior===k?" on":"")+'" data-int="'+k+'">'+
          interiorSVG(k)+'<span class="cn">'+it.name+'</span><span class="cd">'+it.d+'</span></button>';
      }).join("")+'</div>';
    body.querySelectorAll("[data-int]").forEach(function(b){
      b.addEventListener("click", function(){
        WIZ.choiceInterior = this.getAttribute("data-int"); renderWizard(); });
    });
    next.disabled = !WIZ.choiceInterior;
  }
  else if (WIZ.step===3){
    title.textContent = "Revisão";
    // Commit the interior choice into the cfg so the review + build reflect it.
    // A fresh tree invalidates any path the user had selected before opening the
    // wizard, so drop the selection too — closeWizard()'s render() would otherwise
    // walk a stale path (e.g. [0]) into a tree that no longer has those children.
    if (WIZ.choiceInterior){
      state.cfg.layout = INTERIORS[WIZ.choiceInterior].build();
      state.sel = null;
    }
    var c = state.cfg, it = WIZ.choiceInterior?INTERIORS[WIZ.choiceInterior].name:"—";
    body.innerHTML =
      '<p class="steplead">Tudo certo? Gere o armário ou vá para o editor para ajustes finos.</p>'+
      '<div class="review">'+
        rrow("Tipo", WIZ.choicePreset?PRESETS[WIZ.choicePreset].name:"—")+
        rrow("Medidas", fmt(c.W)+" × "+fmt(c.H)+" × "+fmt(c.D)+" mm (L×A×P)")+
        rrow("Material", esc(c.material)+"  ·  "+fmt(c.t)+" mm")+
        rrow("Fundo", c.with_back?"Sim":"Não")+
        rrow("Rodapé", c.with_toe_kick?(fmt(c.toe_kick_height)+" mm"):"Não")+
        rrow("Fita", "corpo "+(FITA_LABEL[(c.fita||{}).carcass]||"—")+"  ·  frentes "+(FITA_LABEL[(c.fita||{}).fronts]||"—"))+
        rrow("Interior", esc(it))+
      '</div>'+
      '<div class="btns" style="margin-top:14px;">'+
        '<button class="btn ghost" id="wizToEditor">Ajustar em detalhe</button></div>';
    byId("wizToEditor").addEventListener("click", closeWizard);
    next.textContent = "Gerar armário";
    next.disabled = false;
    return;
  }
  next.textContent = "Continuar";
}
function wizRow(id,label,unit,val){
  return '<div class="row"><label>'+label+'</label><div class="field has-unit">'+
    '<input type="number" id="'+id+'" step="10" value="'+val+'"><span class="unit">'+unit+'</span></div></div>';
}
function rrow(k,v){ return '<div class="rrow"><span class="rk">'+k+'</span><span class="rv">'+v+'</span></div>'; }
function wizFitaRow(id,label){
  return '<div class="row"><label>'+label+'</label><select id="'+id+'" class="grow">'+
    FMCFG.FITA_CHOICES.map(function(c){
      return '<option value="'+c[0]+'">'+esc(c[1])+'</option>'; }).join("")+'</select></div>';
}
var FITA_LABEL = { none:"Nenhuma", thin:"0,4mm", thick:"1mm" };

function pickPreset(k){
  WIZ.choicePreset = k;
  var p = PRESETS[k];
  if (p.W!=null){ state.cfg.W=p.W; state.cfg.H=p.H; state.cfg.D=p.D; state.cfg.with_toe_kick=p.toe; }
  renderWizard();
}
function wizReadMeasures(){
  var c = state.cfg;
  c.W = parseFloat(byId("wW").value)||c.W;
  c.H = parseFloat(byId("wH").value)||c.H;
  c.D = parseFloat(byId("wD").value)||c.D;
  c.t = parseFloat(byId("wT").value)||c.t;
  var m = byId("wMat"); if (m) c.material = m.value;
  if (!c.fita) c.fita = {};
  var fca = byId("wFitaCarcass"); if (fca) c.fita.carcass = fca.value;
  var ffr = byId("wFitaFronts");  if (ffr) c.fita.fronts  = ffr.value;
  renderDiagram();
  var main = byId("dimDiagram"), wd = byId("wizDiagram");
  if (main && wd) wd.innerHTML = main.innerHTML;
}
function wizAdvance(){
  if (WIZ.step < WIZ_STEPS-1){ WIZ.step++; renderWizard(); return; }
  // final step: build
  closeWizard();
  doApply();
}
