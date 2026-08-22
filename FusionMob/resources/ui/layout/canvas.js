/* =============================================================================
   FusionMob — Cabinet Layout: SVG DRAWING (view layer)
   -----------------------------------------------------------------------------
   Everything this palette draws by hand:

     * render()          the region canvas — the hub every mutation calls back
                         into; it redraws #canvas, rebinds the .leaf clicks,
                         then refreshes the inspector and revalidates.
     * renderInspector() the per-region controls (type tiles, count, size…)
     * renderDiagram()   the live oblique cabinet explainer with W/H/D callouts

   Reads the tree through layout/model.js (`collectRects`, `interiorDims`,
   `TYPE_*`); owns no state of its own.
============================================================================= */
"use strict";

/* ============================================================================
   RENDERING — the region canvas draws readable glyphs per region type
============================================================================ */
function render(){
  if (!state.cfg) return;
  syncFormFromCfg();
  renderDiagram();
  var dim = interiorDims();
  var svg = document.getElementById("canvas");
  if (dim.w <= 0 || dim.h <= 0){ svg.innerHTML =
      '<text x="50" y="50" fill="#e6a23c" font-size="6" text-anchor="middle">Medidas sem interior válido</text>';
      svg.setAttribute("viewBox","0 0 100 100"); renderInspector(); validateNow(); return; }
  svg.setAttribute("viewBox", "0 0 " + dim.w + " " + dim.h);
  var out = [];
  collectRects(state.cfg.layout, [], 0, 0, dim.w, dim.h, dim.t, out);
  var scale = Math.max(dim.w, dim.h);
  var parts = "", selKey = state.sel ? state.sel.join(",") : " ";
  out.forEach(function(r){
    if (r.divider){ parts += rect(r.x, r.y, r.w, r.h, "#8a8a8a", "none", 0, ""); return; }
    var isSel = r.path.join(",") === selKey;
    parts += '<g class="leaf" data-path="'+r.path.join(",")+'">';
    parts += rect(r.x, r.y, r.w, r.h, TYPE_FILL[r.node.type]||"#3a3a3a",
                  isSel ? "#0696d7" : "#666", isSel ? Math.max(dim.t*0.8,scale/90) : scale/220, "");
    parts += leafGlyph(r.node, r.x, r.y, r.w, r.h, scale);
    var cx = r.x + r.w/2, lbl = TYPE_LABEL[r.node.type] || "Vazio";
    if (r.node.type!=="open") lbl += " ("+r.node.count+")";
    parts += '<text class="leaf-label" x="'+cx+'" y="'+(r.y+r.h-scale/38)+'" text-anchor="middle" '+
             'fill="#dcdcdc" font-size="'+(scale/34)+'">'+lbl+'</text>';
    parts += '</g>';
  });
  svg.innerHTML = parts;
  Array.prototype.forEach.call(svg.querySelectorAll(".leaf"), function(g){
    g.addEventListener("click", function(){
      var p = g.getAttribute("data-path");
      state.sel = p === "" ? [] : p.split(",").map(Number);
      renderInspector(); render();
    });
  });
  renderInspector();
  validateNow();
}
function rect(x,y,w,h,fill,stroke,sw,extra){
  return '<rect x="'+x+'" y="'+y+'" width="'+w+'" height="'+h+'" fill="'+fill+
         '" stroke="'+stroke+'" stroke-width="'+sw+'" '+extra+'/>';
}
/* Type-specific mini illustration drawn inside a region rect (mm units). */
function leafGlyph(node, x, y, w, h, scale){
  var n = Math.max(1, node.count||1), sw = scale/260, col = TYPE_COLOR[node.type]||"#888";
  var pad = Math.min(w,h)*0.10, s = "";
  var ix=x+pad, iy=y+pad, iw=w-2*pad, ih=h-2*pad;
  if (iw<=0||ih<=0) return "";
  if (node.type==="shelves"){
    for (var i=1;i<=n;i++){
      var yy = y + h*i/(n+1);
      s += '<line x1="'+ix+'" y1="'+yy+'" x2="'+(x+w-pad)+'" y2="'+yy+'" stroke="'+col+'" stroke-width="'+(sw*2.4)+'"/>';
    }
  } else if (node.type==="doors"){
    var dw = iw/n;
    for (var d=0; d<n; d++){
      var dx = ix + d*dw;
      s += '<rect x="'+(dx+sw*2)+'" y="'+iy+'" width="'+(dw-sw*4)+'" height="'+ih+'" fill="none" stroke="'+col+'" stroke-width="'+(sw*2)+'"/>';
      // handle on the swing edge (left doors hinge left => handle right, etc.)
      var hx = (d < n/2) ? (dx+dw-sw*7) : (dx+sw*7);
      s += '<circle cx="'+hx+'" cy="'+(y+h/2)+'" r="'+(sw*3)+'" fill="'+col+'"/>';
    }
  } else if (node.type==="drawers"){
    var dh = ih/n;
    for (var k=0;k<n;k++){
      var dy = iy + k*dh;
      s += '<rect x="'+ix+'" y="'+(dy+sw*2)+'" width="'+iw+'" height="'+(dh-sw*4)+'" fill="none" stroke="'+col+'" stroke-width="'+(sw*2)+'"/>';
      s += '<line x1="'+(x+w/2-iw*0.16)+'" y1="'+(dy+dh/2)+'" x2="'+(x+w/2+iw*0.16)+'" y2="'+(dy+dh/2)+'" stroke="'+col+'" stroke-width="'+(sw*3)+'"/>';
    }
  } else if (node.type==="blind"){
    // Solid panel blocking access: a filled front with diagonal hatching.
    s += '<rect x="'+ix+'" y="'+iy+'" width="'+iw+'" height="'+ih+'" fill="'+col+'" fill-opacity="0.22" stroke="'+col+'" stroke-width="'+(sw*2)+'"/>';
    var step = Math.max(iw,ih)/6;
    for (var g = -ih; g < iw; g += step){
      var ax = ix + Math.max(0, g),            ay = iy + Math.max(0, -g);
      var bx = ix + Math.min(iw, g + ih),      by = iy + Math.min(ih, iw - g);
      s += '<line x1="'+ax+'" y1="'+ay+'" x2="'+bx+'" y2="'+by+'" stroke="'+col+'" stroke-width="'+(sw*1.4)+'"/>';
    }
  }
  return s;
}

/* ---- inspector (per-region controls) ---------------------------------------- */
function renderInspector(){
  var sel = state.sel, has = sel !== null;
  var node = has ? getNode(sel) : null;
  var leaf = node && !isSplit(node);
  document.getElementById("selInfo").innerHTML =
    !has ? "interior inteiro"
         : (leaf ? '<span class="swatch" style="background:'+(TYPE_COLOR[node.type]||"#888")+'"></span>'+(TYPE_LABEL[node.type]||"Vazio")
                 : "grupo (dividido)");

  // type tiles
  ["open","shelves","doors","drawers","blind"].forEach(function(tp){
    var el = document.getElementById("tile"+tp.charAt(0).toUpperCase()+tp.slice(1));
    el.disabled = !leaf;
    el.classList.toggle("on", leaf && node.type===tp);
  });

  var isDoors = leaf && node.type==="doors";
  var isDrawers = leaf && node.type==="drawers";
  var isShelves = leaf && node.type==="shelves";
  var isBlind = leaf && node.type==="blind";
  var counted = leaf && node.type!=="open" && node.type!=="blind";
  show("rowCount", counted);
  show("rowShelvesBehind", isDoors);
  show("rowInset", isDoors || isDrawers || isBlind);
  show("rowAlign", isShelves);

  var wrap = has ? getWrapper(sel) : null;
  show("rowSize", !!wrap);
  setDisabled("splitV", !leaf); setDisabled("splitH", !leaf);
  setDisabled("del", !has || sel.length===0);

  if (leaf){
    document.getElementById("leafCount").value = node.count||1;
    document.getElementById("leafShelvesBehind").value = node.shelves_behind||0;
    document.getElementById("leafInset").checked = !!node.inset;
    var al = node.shelf_align_front;
    document.getElementById("leafAlign").checked = (al===null||al===undefined) ? false : !!al;
  }
  if (wrap){
    document.getElementById("leafSize").value = Math.round(wrap.size*10)/10;
    document.getElementById("leafFixed").checked = !!wrap.fixed;
    document.getElementById("leafSizeUnit").textContent = wrap.fixed ? "mm" : "peso";
  }
}

/* ============================================================================
   DIMENSION DIAGRAM — an explainer oblique cabinet with dimension callouts
============================================================================ */
function renderDiagram(){
  var svg = document.getElementById("dimDiagram");
  if (!svg) return;
  // Fixed stylized oblique box (not to scale) with W/A/P callouts.
  var fx=42, fy=52, fw=118, fh=110, dx=42, dy=-30;   // front rect + depth vector
  var bx=fx+dx, by=fy+dy;                            // back-top-left
  svg.innerHTML =
    // top face
    '<polygon class="cab-top" points="'+fx+','+fy+' '+(fx+fw)+','+fy+' '+(fx+fw+dx)+','+(fy+dy)+' '+(fx+dx)+','+(fy+dy)+'"/>'+
    // right side face
    '<polygon class="cab-side" points="'+(fx+fw)+','+fy+' '+(fx+fw+dx)+','+(fy+dy)+' '+(fx+fw+dx)+','+(fy+fh+dy)+' '+(fx+fw)+','+(fy+fh)+'"/>'+
    // front face
    '<rect class="cab-face" x="'+fx+'" y="'+fy+'" width="'+fw+'" height="'+fh+'" rx="1"/>'+
    // thickness hint (front-left edge)
    '<rect x="'+fx+'" y="'+fy+'" width="7" height="'+fh+'" fill="#555" stroke="#666" data-dim="t"/>'+
    // Width dimension (below front)
    '<g class="dim" data-dim="W"><line x1="'+fx+'" y1="'+(fy+fh+16)+'" x2="'+(fx+fw)+'" y2="'+(fy+fh+16)+'"/>'+
      '<line x1="'+fx+'" y1="'+(fy+fh+11)+'" x2="'+fx+'" y2="'+(fy+fh+21)+'"/>'+
      '<line x1="'+(fx+fw)+'" y1="'+(fy+fh+11)+'" x2="'+(fx+fw)+'" y2="'+(fy+fh+21)+'"/>'+
      '<text x="'+(fx+fw/2)+'" y="'+(fy+fh+30)+'" text-anchor="middle" id="dimTextW">L</text></g>'+
    // Height dimension (left of front)
    '<g class="dim" data-dim="H"><line x1="'+(fx-16)+'" y1="'+fy+'" x2="'+(fx-16)+'" y2="'+(fy+fh)+'"/>'+
      '<line x1="'+(fx-21)+'" y1="'+fy+'" x2="'+(fx-11)+'" y2="'+fy+'"/>'+
      '<line x1="'+(fx-21)+'" y1="'+(fy+fh)+'" x2="'+(fx-11)+'" y2="'+(fy+fh)+'"/>'+
      '<text x="'+(fx-22)+'" y="'+(fy+fh/2)+'" text-anchor="end" id="dimTextH">A</text></g>'+
    // Depth dimension (along top-right oblique)
    '<g class="dim" data-dim="D"><line x1="'+(fx+fw+6)+'" y1="'+(fy+3)+'" x2="'+(bx+fw+6)+'" y2="'+(by+3)+'"/>'+
      '<text x="'+(fx+fw+dx/2+12)+'" y="'+(fy+dy/2+2)+'" id="dimTextD">P</text></g>';
  // fill in current values
  var c = state.cfg;
  if (c){
    setText("dimTextW","L "+fmt(c.W));
    setText("dimTextH","A "+fmt(c.H));
    setText("dimTextD","P "+fmt(c.D));
  }
}
function fmt(v){ return (Math.round(v)||0)+""; }
function setText(id,t){ var el=document.getElementById(id); if(el) el.textContent=t; }
function highlightDim(which, on){
  Array.prototype.forEach.call(document.querySelectorAll("#dimDiagram .dim"), function(g){
    g.classList.toggle("active", on && g.getAttribute("data-dim")===which);
  });
}
