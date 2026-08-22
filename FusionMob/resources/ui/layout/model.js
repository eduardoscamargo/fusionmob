/* =============================================================================
   FusionMob — Cabinet Layout: the interior REGION TREE (model layer)
   -----------------------------------------------------------------------------
   The cabinet interior is a recursive tree: a node is either a SPLIT
   ({split:'v'|'h', children:[{size,fixed,node},…]}) or a LEAF
   ({type:'open'|'shelves'|'doors'|'drawers'|'blind', count, inset, …}).
   A 'v' split stacks rows, an 'h' split places columns; a divider of carcass
   thickness sits between consecutive children.

   Everything here is pure data manipulation over `state.cfg.layout` plus the
   geometry that turns the tree into flat rectangles — no DOM, no SVG. The
   drawing lives in layout/canvas.js, which consumes `collectRects`.

   Paths are arrays of child indices; [] is the root.
============================================================================= */
"use strict";

/* Region type presentation. Colors reference the CSS custom-property palette so
   the canvas glyphs match the type tiles. */
var TYPE_COLOR = { open:"#3f3f3f", shelves:"#2f5d78", doors:"#2f6b4a", drawers:"#8a5a24", blind:"#8a7f73" };
var TYPE_FILL  = { open:"#3a3a3a", shelves:"#26424f", doors:"#264d3a", drawers:"#5c421f", blind:"#4a453e" };
var TYPE_LABEL = { open:"Vazio", shelves:"Prateleiras", doors:"Portas", drawers:"Gavetas", blind:"Cego" };

/* ============================================================================
   LAYOUT TREE HELPERS  (paths are arrays of child indices)
============================================================================ */
function isSplit(n){ return n && n.split !== undefined; }
function getNode(path){
  var n = state.cfg.layout;
  for (var i=0;i<path.length;i++){ n = n.children[path[i]].node; }
  return n;
}
function getWrapper(path){          // {size,fixed,node} of the child at path, or null for root
  if (!path.length) return null;
  var p = getNode(path.slice(0,-1));
  return p.children[path[path.length-1]];
}
function setNode(path, val){
  if (!path.length){ state.cfg.layout = val; return; }
  var p = getNode(path.slice(0,-1));
  p.children[path[path.length-1]].node = val;
}
function newLeaf(type){ return { type:type||"open", count:1, inset:false, gap:null,
                                 shelf_align_front:null, slide_key:null, shelves_behind:0 }; }
function splitLeaf(path, axis){
  var node = getNode(path);
  setNode(path, { split:axis, children:[
    { size:1, fixed:false, node:node },
    { size:1, fixed:false, node:newLeaf() } ] });
  state.sel = path.concat([0]);
}
function deleteRegion(path){
  if (!path.length) return;                       // can't remove the whole interior
  var parentPath = path.slice(0,-1);
  var parent = getNode(parentPath);
  parent.children.splice(path[path.length-1], 1);
  if (parent.children.length === 1){              // collapse a now-single split
    setNode(parentPath, parent.children[0].node);
    state.sel = parentPath;
  } else {
    state.sel = parentPath.length ? parentPath : null;
  }
}

/* ---- interior geometry (mm, y downward: 0 = top) ---------------------------- */
function interiorDims(){
  var c = state.cfg, t = c.t;
  var kick = c.with_toe_kick ? c.toe_kick_height : 0;
  var Hbox = c.H - kick;
  return { w:(c.W - 2*t), h:(Hbox - 2*t), t:t };
}
function childSizes(children, L, t){
  var k = children.length, avail = L - (k-1)*t;
  var fixedSum = 0, flexTotal = 0;
  children.forEach(function(c){ if (c.fixed) fixedSum += c.size; else flexTotal += c.size; });
  flexTotal = flexTotal || 1;
  var leftover = avail - fixedSum;
  return children.map(function(c){
    return c.fixed ? c.size : Math.max(0, leftover) * c.size / flexTotal;
  });
}
function collectRects(node, path, x, y, w, h, t, out){
  if (!isSplit(node)){ out.push({path:path, node:node, x:x, y:y, w:w, h:h}); return; }
  var axis = node.split, ch = node.children;
  if (axis === "h"){                               // columns, left -> right
    var sizes = childSizes(ch, w, t), cx = x;
    for (var i=0;i<ch.length;i++){
      collectRects(ch[i].node, path.concat([i]), cx, y, sizes[i], h, t, out);
      cx += sizes[i];
      if (i < ch.length-1){ out.push({divider:true, x:cx, y:y, w:t, h:h}); cx += t; }
    }
  } else {                                         // rows, first child = BOTTOM
    var sizesv = childSizes(ch, h, t), cyBottom = y + h;
    for (var j=0;j<ch.length;j++){
      var hh = sizesv[j];
      collectRects(ch[j].node, path.concat([j]), x, cyBottom - hh, w, hh, t, out);
      cyBottom -= hh;
      if (j < ch.length-1){ out.push({divider:true, x:x, y:cyBottom - t, w:w, h:t}); cyBottom -= t; }
    }
  }
}
