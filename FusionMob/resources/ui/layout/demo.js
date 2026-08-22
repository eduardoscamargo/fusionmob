/* =============================================================================
   FusionMob — Cabinet Layout: DEMO MODE (browser fallback)
   -----------------------------------------------------------------------------
   When `adsk` never answers (the page is open in a plain browser rather than a
   Fusion palette), init() gives up after its retries and calls enterDemo() so
   the UI is still explorable. `demoCfg()` mirrors the normalized DEFAULT_CFG in
   FusionMob.py — keep the two in step when a cfg key is added.

   Nothing here runs inside Fusion; Aplicar refuses in demo mode.
============================================================================= */
"use strict";

/* ---- a JS default cfg for demo/browser mode (mirrors normalized DEFAULT_CFG) - */
function demoCfg(){
  return {
    W:800,H:2100,D:400,t:18, n_shelves:3, material:"MDF 18mm Branco", shelf_align_front:false,
    with_back:true, back_mode:"groove", back_material:"MDF 6mm Cru", back_t:6, dado_depth:8, back_setback:10,
    with_toe_kick:true, toe_kick_material:"MDF 18mm Branco", toe_kick_t:18, toe_kick_height:100,
    toe_kick_setback:75, toe_kick_max_span:500,
    with_doors:false, door_material:"MDF 18mm Branco", door_t:18, n_doors:2, door_gap:3,
    door_inset:false, with_hinges:true,
    hinge:{cup_diameter:35,cup_depth:12,cup_edge:22.5,end_inset:100,screw_diameter:5,
           screw_depth:12,plate_front:37,screw_pitch:32,shelf_clearance:30},
    with_drawers:false, n_drawers:3, drawer_inset:false, drawer_gap:3,
    slide_key:"telescopica_h45_350", insert_real_hardware:false,
    drawer:{box_material:"MDF 16mm Branco",box_t:16,bottom_material:"MDF 6mm Cru",bottom_t:6,
            bottom_dado_depth:6,bottom_up:12,bottom_play:0.4,box_height:150,box_top_gap:30,
            face_material:"MDF 18mm Branco",face_t:18,
            back_height_reduction:0},
    slide:{custom:false,side_space:12.7,bottom_clearance:3,back_clearance:10,
           box_depth:350,min_cabinet_depth:380},
    fita:{name_thin:"Fita PVC 0.4mm Branco",name_thick:"Fita PVC 1mm Branco",
          carcass:"thin",fronts:"thick"},
    joinery:{bottom_mode:"aligned",bottom_overhang:0,top_mode:"aligned",top_overhang:0,sides_to_floor:false},
    tamponamento:{left:false,right:false,top:false,t:18,material:"",front_overhang:0},
    arremate:{top:false,top_gap_mode:"gap",top_gap:50,ceiling_height:2400,top_inline_fronts:false,top_side_returns:true,left:false,right:false,side_gap:30,t:18,material:""},
    puxador:{enabled:false,side:"bottom",size:40},
    tol:{dado_bottom_clearance:0.5,dado_side_clearance:0.2,shelf_back_gap:1,
         shelf_front_setback:30,shelf_door_clearance:2},
    layout:{type:"shelves",count:3,inset:false,gap:null,shelf_align_front:null,
            slide_key:null,shelves_behind:0}
  };
}
function enterDemo(){
  state.demo = true;
  state.materials = ["MDF 18mm Branco","MDF 15mm Branco","MDF 16mm Branco","MDF 6mm Cru","MDP 18mm Branco"];
  state.slides = [
    {key:"telescopica_h45_350", desc:"Corrediça telescópica H45 350mm (ext. total, lateral)",
     mount:"side", side_space:12.7, bottom_clearance:3, back_clearance:10,
     box_depth:350, min_cabinet_depth:380},
    {key:"roldana_400", desc:"Corrediça de roldana 400mm (ext. parcial, lateral)",
     mount:"side", side_space:12.5, bottom_clearance:3, back_clearance:10,
     box_depth:400, min_cabinet_depth:430},
    {key:"oculta_softclose_400", desc:"Corrediça oculta soft-close 400mm (ext. total, sob a gaveta)",
     mount:"undermount", side_space:6.5, bottom_clearance:13, back_clearance:20,
     box_depth:400, min_cabinet_depth:420}];
  state.fita_choices = [];
  state.cabinets = []; state.cfg = demoCfg(); state.target="new"; state.sel=null;
  byId("demoBanner").classList.add("show");
  syncCtx(); fillTargets(); render();
  setStatus("Modo demonstração. Abra pelo comando Cabinet Layout no Fusion para gerar.", "warn");
}
