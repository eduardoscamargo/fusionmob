/* =============================================================================
   FusionMob — SHARED cabinet-configuration UI (single source of truth)
   -----------------------------------------------------------------------------
   Every cabinet configuration knob is declared exactly ONCE, here, in `SPEC`:
   its cfg path, DOM id, label, type, unit, help tooltip, the section it belongs
   to and the reference diagram that illustrates it. Both palettes render their
   configuration form from this file:

     * preferences.html  — surface "prefs"    (the defaults for every new cabinet)
     * layout_editor.html — surface "cabinet"  (per-cabinet overrides of those)

   That is the whole point: adding a field, renaming a label, fixing a tooltip or
   redrawing a schematic happens here and lands on BOTH pages at once. Never add
   a configuration row directly to either HTML file — see resources/ui/CLAUDE.md.

   No dependencies, no build step: the palettes load this with a plain
   <script src="cabinet_config.js"> (same folder, so the relative file:// URL
   Fusion hands the palette resolves).
============================================================================= */
"use strict";

var FMCFG = (function () {

  /* ---- nested-path get/set on a cfg object --------------------------------- */
  function getPath(obj, path) {
    var parts = String(path).split("."), n = obj;
    for (var i = 0; i < parts.length; i++) { if (n == null) return undefined; n = n[parts[i]]; }
    return n;
  }
  function setPath(obj, path, val) {
    var parts = String(path).split("."), n = obj;
    for (var i = 0; i < parts.length - 1; i++) {
      if (n[parts[i]] == null || typeof n[parts[i]] !== "object") n[parts[i]] = {};
      n = n[parts[i]];
    }
    n[parts[parts.length - 1]] = val;
  }

  /* ---- fixed option lists (mirror the Python FITA_CHOICES / *_CHOICES) ------ */
  var FITA_CHOICES = [["none", "Nenhuma"], ["thin", "0,4mm (fina)"], ["thick", "1mm (grossa)"]];
  var JOINERY_CHOICES = [["aligned", "Alinhada com base"], ["over", "Sobre base"]];
  var BACK_MODE_CHOICES = [["groove", "Encaixado (ranhura)"], ["overlay", "Sobreposto (atrás)"]];
  var ARREMATE_MODE_CHOICES = [["gap", "Informar folga"], ["ceiling", "Informar altura do teto"]];
  var INHERIT_LABEL = "(mesmo do corpo)";

  /* ===========================================================================
     THE SPEC — every cabinet configuration field, declared once.

     Section: { key, title, tag, diagram, hint, open, notes:{surface:text},
                fields:[…] }
     Field:   { id, p, l, t, u, h, … }
       id   DOM id of the control (stable; the pages wire behaviour to these)
       p    cfg path ("arremate.top_gap")
       l    label
       t    num | int | text | bool | sel | material | material_inherit | slide
            | fita | note  ("note" renders an empty <p> the page fills at runtime)
       u    unit suffix        h  tooltip text        opts  [[value,label],…]
       step/min  number-input attributes
       rowId     stable id for the row (so behaviour can show/hide it)
       only      'prefs' | 'cabinet' — render on that surface only
       inDiagram true = the control lives inside the section's diagram, not in a
                 row of its own (the two Fixação lateral overhangs)
  =========================================================================== */
  var SPEC = [
    /* ---------------------------------------------------------------- dims -- */
    {
      key: "dims", title: "Medidas e material", tag: "— dimensões externas",
      diagram: "dims", open: true,
      notes: {
        cabinet: "Prateleiras, portas e gavetas são definidas por região na seção " +
                 "Interior."
      },
      fields: [
        { id: "W", p: "W", l: "Largura (L)", t: "num", u: "mm", step: 10,
          h: "Largura externa do armário (dimensão frontal, esquerda→direita), em mm." },
        { id: "H", p: "H", l: "Altura (A)", t: "num", u: "mm", step: 10,
          h: "Altura externa total, JÁ incluindo o rodapé, em mm." },
        { id: "D", p: "D", l: "Profundidade (P)", t: "num", u: "mm", step: 10,
          h: "Profundidade externa (da frente até o fundo), em mm." },
        { id: "material", p: "material", l: "Material da carcaça", t: "material",
          h: "Chapa usada no corpo (laterais/base/tampo). Vem da sua biblioteca de materiais." },
        { id: "t", p: "t", l: "Espessura da carcaça", t: "num", u: "mm", step: 1,
          h: "Grossura da chapa das laterais, base e tampo (ex.: 18 mm). Cada peça pode " +
             "ter sua própria espessura no corte." },
        { id: "n_shelves", p: "n_shelves", l: "Prateleiras", t: "int", only: "prefs",
          h: "Número de prateleiras internas distribuídas igualmente. Vale quando o armário " +
             "é uma única região; no editor de Layout a quantidade é por região." },
        { id: "shelf_align_front", p: "shelf_align_front", l: "Prateleiras na frente",
          t: "bool", only: "prefs",
          h: "Ligado: prateleiras alinhadas com a frente do corpo. Desligado: recuadas da " +
             "frente (recuo padrão ~30 mm). No editor de Layout isto é por região." }
      ]
    },

    /* ---------------------------------------------------------------- fita -- */
    {
      key: "fita", title: "Fita de borda", tag: "— quais bordas levam fita",
      diagram: "fita", open: true,
      hint: "A borda frontal do corpo (laterais/base/tampo/prateleiras/divisórias) e o topo " +
            "da frente do rodapé recebem fita automaticamente; portas e frentes de gaveta " +
            "levam fita nos quatro lados. Escolha a espessura por grupo (ou Nenhuma).",
      fields: [
        { id: "fita_carcass", p: "fita.carcass", l: "Bordas do corpo", t: "fita",
          h: "Espessura da fita nas bordas visíveis do corpo (borda frontal de laterais/base/" +
             "tampo/prateleiras/divisórias). Nenhuma, 0,4mm ou 1mm." },
        { id: "fita_fronts", p: "fita.fronts", l: "Frentes (portas/gavetas/rodapé)", t: "fita",
          h: "Espessura da fita nas frentes — portas, frentes de gaveta e topo do rodapé — " +
             "fitadas nos quatro lados." },
        { id: "fita_name_thin", p: "fita.name_thin", l: "Nome da fita 0,4mm", t: "text",
          h: "Nome da fita de 0,4 mm exatamente como deve aparecer no CorteCloud " +
             "(ex.: “Fita PVC 0.4mm Branco”)." },
        { id: "fita_name_thick", p: "fita.name_thick", l: "Nome da fita 1mm", t: "text",
          h: "Nome da fita de 1 mm como deve aparecer no CorteCloud." }
      ]
    },

    /* ---------------------------------------------------------------- back -- */
    {
      key: "back", title: "Fundo", tag: "— painel traseiro", diagram: "back",
      fields: [
        { id: "with_back", p: "with_back", l: "Tem fundo", t: "bool",
          h: "Adiciona um painel de fundo ao corpo do armário." },
        { id: "back_mode", p: "back_mode", l: "Fixação do fundo", t: "sel",
          opts: BACK_MODE_CHOICES,
          h: "Como o fundo é fixado. Encaixado: assenta numa ranhura (dado) nas laterais/" +
             "base/tampo. Sobreposto: aplicado (aparafusado) na face traseira, em toda a " +
             "largura, sem ranhura." },
        { id: "back_material", p: "back_material", l: "Material do fundo", t: "material",
          h: "Chapa do painel de fundo (normalmente fina, ex.: 6 mm cru)." },
        { id: "back_t", p: "back_t", l: "Espessura do fundo", t: "num", u: "mm", step: 1,
          h: "Grossura do painel de fundo, em mm." },
        { id: "back_setback", p: "back_setback", l: "Recuo do fundo", t: "num", u: "mm",
          step: 1, rowId: "rowBackSetback",
          h: "Distância da face traseira do corpo até o fundo, em mm (só no modo encaixado)." },
        { id: "dado_depth", p: "dado_depth", l: "Profund. da ranhura", t: "num", u: "mm",
          step: 1, rowId: "rowDadoDepth",
          h: "Profundidade do rasgo (dado) onde o fundo encaixa, cortado nas laterais/base/" +
             "tampo (só no modo encaixado)." }
      ]
    },

    /* ---------------------------------------------------------------- kick -- */
    {
      key: "kick", title: "Rodapé", tag: "— base de apoio", diagram: "kick",
      fields: [
        { id: "with_toe_kick", p: "with_toe_kick", l: "Tem rodapé", t: "bool",
          h: "Adiciona um rodapé (caixa separada) sob o armário. A altura total do armário " +
             "já inclui o rodapé." },
        { id: "toe_kick_material", p: "toe_kick_material", l: "Material do rodapé",
          t: "material", h: "Chapa usada nas peças do rodapé." },
        { id: "toe_kick_height", p: "toe_kick_height", l: "Altura do rodapé", t: "num",
          u: "mm", step: 1,
          h: "Altura do recuo do rodapé sob o corpo, em mm (padrão BR ~100 mm)." },
        { id: "toe_kick_t", p: "toe_kick_t", l: "Espessura do rodapé", t: "num", u: "mm",
          step: 1, h: "Grossura da chapa do rodapé, em mm." },
        { id: "toe_kick_setback", p: "toe_kick_setback", l: "Recuo do rodapé", t: "num",
          u: "mm", step: 1,
          h: "Quanto a tábua frontal do rodapé recua em relação à frente do armário, em mm." },
        { id: "toe_kick_max_span", p: "toe_kick_max_span", l: "Vão máx. sem reforço",
          t: "num", u: "mm", step: 10,
          h: "Comprimento máximo do rodapé antes de adicionar um reforço interno, em mm." }
      ]
    },

    /* ------------------------------------------------------------- joinery -- */
    {
      key: "joinery", title: "Fixação lateral", tag: "— lateral × base/tampo",
      diagram: "joinery_live",
      hint: "Como a lateral encontra a base e o tampo. <b>Alinhada com base</b>: base/tampo " +
            "entre as laterais (laterais inteiras). <b>Sobre base</b>: base/tampo em toda a " +
            "largura, com a lateral apoiada sobre/sob o painel. O avanço (mm) é o quanto a " +
            "lateral avança sobre o painel — edite direto no desenho.",
      fields: [
        { id: "joineryTopOverhang", p: "joinery.top_overhang", t: "num", inDiagram: true,
          h: "Avanço (mm): quanto a lateral avança sobre o tampo. 0 = apoia sob o painel; " +
             "= espessura = faceada com a face externa. Só vale em “Sobre base”." },
        { id: "joineryBottomOverhang", p: "joinery.bottom_overhang", t: "num", inDiagram: true,
          h: "Avanço (mm): quanto a lateral avança sobre a base. 0 = apoia sobre o painel; " +
             "= espessura = faceada com a face externa. Só vale em “Sobre base”." },
        { id: "joineryTopMode", p: "joinery.top_mode", l: "Base superior (tampo)", t: "sel",
          opts: JOINERY_CHOICES,
          h: "Como o tampo encontra as laterais. Alinhada: tampo entre as laterais (laterais " +
             "inteiras). Sobre base: tampo em toda a largura, com a lateral apoiada sob ele." },
        { id: "joineryBottomMode", p: "joinery.bottom_mode", l: "Base inferior", t: "sel",
          opts: JOINERY_CHOICES,
          h: "Como a base encontra as laterais. Alinhada: base entre as laterais (laterais " +
             "inteiras). Sobre base: base em toda a largura, com a lateral apoiada sobre ela." },
        { id: "joinerySidesToFloor", p: "joinery.sides_to_floor",
          l: "Laterais até o piso (pés)", t: "bool",
          h: "Ligado: as laterais descem até o chão como pés e o rodapé fica recuado entre " +
             "elas. Desligado: as laterais param na base e o rodapé é uma caixa separada em " +
             "toda a largura. Requer rodapé." }
      ]
    },

    /* -------------------------------------------------------- tamponamento -- */
    {
      key: "tamponamento", title: "Tamponamento (acabamento)", tag: "— painéis de acabamento",
      diagram: "tamponamento",
      hint: "Painéis de acabamento aplicados sobre as faces expostas (laterais e/ou tampo). " +
            "Cada face ligada acrescenta um painel <b>por fora</b> da estrutura, aumentando as " +
            "dimensões externas. As laterais vão do piso ao topo em toda a profundidade; o " +
            "tampo cobre toda a largura acabada. O interior não é afetado.",
      fields: [
        { id: "tampLeft", p: "tamponamento.left", l: "Lado esquerdo", t: "bool",
          h: "Painel de acabamento aplicado sobre a lateral esquerda (por fora da estrutura; " +
             "aumenta a largura externa)." },
        { id: "tampRight", p: "tamponamento.right", l: "Lado direito", t: "bool",
          h: "Painel de acabamento aplicado sobre a lateral direita (por fora da estrutura; " +
             "aumenta a largura externa)." },
        { id: "tampTop", p: "tamponamento.top", l: "Superior", t: "bool",
          h: "Painel de acabamento aplicado sobre o tampo (por fora da estrutura; aumenta a " +
             "altura externa)." },
        { id: "tampThickness", p: "tamponamento.t", l: "Espessura", t: "num", u: "mm",
          step: 1, min: 0, h: "Grossura da chapa do tamponamento, em mm." },
        { id: "tampFrontOverhang", p: "tamponamento.front_overhang", l: "Avanço frontal",
          t: "num", u: "mm", step: 1, min: 0,
          h: "Quanto o tamponamento avança à frente da carcaça (0 = faceado). A traseira fica " +
             "alinhada com o fundo, então o painel fica mais fundo que a carcaça." },
        { id: "tampMaterial", p: "tamponamento.material", l: "Material", t: "material_inherit",
          h: "Chapa do tamponamento. Escolha “" + INHERIT_LABEL + "” para herdar o material " +
             "da carcaça." }
      ]
    },

    /* ------------------------------------------------------------ arremate -- */
    {
      key: "arremate", title: "Arremate (ajuste)", tag: "— até o teto / paredes",
      diagram: "arremate",
      hint: "Peças de <b>arremate</b> para o armário “alcançar” um teto ou paredes de medida " +
            "incerta: monte a carcaça um pouco menor e feche as folgas com peças frontais " +
            "cortadas com sobra e ajustadas no local. A <b>sanefa</b> superior sobe do topo " +
            "até o teto (dá aparência de altura total); as <b>réguas</b> laterais preenchem a " +
            "folga até a parede. Ficam <b>por fora</b> da carcaça (aumentam as dimensões " +
            "externas); o interior não é afetado.",
      fields: [
        { id: "arrTop", p: "arremate.top", l: "Sanefa superior (até o teto)", t: "bool",
          h: "Peça frontal (sanefa) sobre o tampo, do topo do armário até o teto. Fecha a " +
             "folga vista de frente, dando aparência de altura total. Cortada com folga e " +
             "ajustada no local." },
        { id: "arrTopMode", p: "arremate.top_gap_mode", l: "Medida da sanefa", t: "sel",
          opts: ARREMATE_MODE_CHOICES,
          h: "Como a altura da sanefa é informada. Informar folga: você digita a folga entre " +
             "o topo do armário e o teto. Informar altura do teto: você digita a altura total " +
             "do ambiente (piso→teto) e a folga é calculada (altura do teto − altura do armário)." },
        { id: "arrTopGap", p: "arremate.top_gap", l: "Folga até o teto", t: "num", u: "mm",
          step: 1, min: 0, rowId: "rowArrTopGap",
          h: "Altura da sanefa = folga entre o topo do armário e o teto, em mm. Aumenta a " +
             "altura externa." },
        { id: "arrCeilingHeight", p: "arremate.ceiling_height", l: "Altura do teto (piso→teto)",
          t: "num", u: "mm", step: 1, min: 0, rowId: "rowArrCeiling",
          h: "Altura total do ambiente do piso até o teto, em mm. A folga da sanefa é " +
             "calculada como esta altura menos a altura (A) do armário." },
        { id: "arrCeilingCalc", t: "note" },
        { id: "arrTopInline", p: "arremate.top_inline_fronts", l: "Sanefa faceada com as frentes",
          t: "bool",
          h: "Com portas/gavetas sobrepostas, a sanefa acompanha o plano das frentes em vez de " +
             "ficar recuada na frente da carcaça. Acrescenta uma segunda chapa à frente da " +
             "sanefa para completar a profundidade até as frentes." },
        { id: "arrTopUShape", p: "arremate.top_side_returns", l: "Sanefa em U (retornos laterais)",
          t: "bool",
          h: "Fecha a sanefa em U: acrescenta um retorno de profundidade total sobre cada " +
             "lateral, para as laterais expostas parecerem continuar até o teto. A tábua " +
             "frontal passa a vão apenas entre os retornos. Ligado por padrão." },
        { id: "arrLeft", p: "arremate.left", l: "Régua esquerda (até a parede)", t: "bool",
          h: "Régua frontal de arremate no lado esquerdo, para preencher a folga até a parede. " +
             "Cortada com folga e escribada na parede." },
        { id: "arrRight", p: "arremate.right", l: "Régua direita (até a parede)", t: "bool",
          h: "Régua frontal de arremate no lado direito, para preencher a folga até a parede." },
        { id: "arrSideGap", p: "arremate.side_gap", l: "Folga até a parede (cada lado)",
          t: "num", u: "mm", step: 1, min: 0,
          h: "Largura de cada régua lateral = folga entre a lateral do armário e a parede, em " +
             "mm. Aumenta a largura externa por lado ligado." },
        { id: "arrThickness", p: "arremate.t", l: "Espessura", t: "num", u: "mm", step: 1,
          min: 0, h: "Grossura da chapa das peças de arremate, em mm." },
        { id: "arrMaterial", p: "arremate.material", l: "Material", t: "material_inherit",
          h: "Chapa das peças de arremate. Escolha “" + INHERIT_LABEL + "” para herdar o " +
             "material da carcaça." }
      ]
    },

    /* --------------------------------------------------------------- doors -- */
    {
      key: "doors", title: "Portas", tag: "— frentes de abrir", diagram: "doors",
      notes: {
        cabinet: "Quantidade, embutida/sobreposta e prateleiras atrás são definidas por " +
                 "região no editor de Interior acima."
      },
      fields: [
        { id: "with_doors", p: "with_doors", l: "Com portas", t: "bool", only: "prefs",
          h: "Padrão de gerar portas frontais em novos armários (regiões de porta no editor " +
             "de Layout sobrepõem isto)." },
        { id: "n_doors", p: "n_doors", l: "Número de portas", t: "int", only: "prefs",
          h: "Quantidade de portas iguais na frente. Duas portas se encontram no centro." },
        { id: "door_inset", p: "door_inset", l: "Porta embutida (inset)", t: "bool",
          only: "prefs",
          h: "Ligado (embutida): a porta assenta dentro do vão, faceada com a frente. " +
             "Desligado (sobreposta): cobre a frente do corpo." },
        { id: "door_material", p: "door_material", l: "Material da porta", t: "material",
          h: "Chapa usada nas portas." },
        { id: "door_t", p: "door_t", l: "Espessura da porta", t: "num", u: "mm", step: 1,
          h: "Grossura da chapa da porta, em mm." },
        { id: "door_gap", p: "door_gap", l: "Folga entre portas", t: "num", u: "mm", step: 0.5,
          h: "Fresta (reveal) entre as portas e nas bordas, em mm." },
        { id: "with_hinges", p: "with_hinges", l: "Furação de dobradiça", t: "bool",
          h: "Fura o caneco de 35 mm nas portas e os pilotos da placa nas laterais (dobradiça " +
             "de caneco oculta). Só geometria — não vai ao CorteCloud." }
      ]
    },

    /* ------------------------------------------------------------- drawers -- */
    {
      key: "drawers", title: "Gavetas", tag: "— caixas, frentes, corrediças",
      diagram: "drawers",
      notes: {
        cabinet: "Quantidade e embutida/sobreposta são definidas por região no editor de " +
                 "Interior acima."
      },
      fields: [
        { id: "with_drawers", p: "with_drawers", l: "Com gavetas", t: "bool", only: "prefs",
          h: "Padrão de gerar uma coluna de gavetas em novos armários (regiões de gaveta no " +
             "editor de Layout sobrepõem isto)." },
        { id: "n_drawers", p: "n_drawers", l: "Número de gavetas", t: "int", only: "prefs",
          h: "Quantidade de gavetas empilhadas na coluna." },
        { id: "drawer_inset", p: "drawer_inset", l: "Gaveta embutida (inset)", t: "bool",
          only: "prefs",
          h: "Ligado (embutida): frentes assentam dentro do vão. Desligado (sobreposta): " +
             "frentes cobrem a frente do corpo." },
        { id: "drawer_gap", p: "drawer_gap", l: "Folga entre gavetas", t: "num", u: "mm",
          step: 0.5, h: "Fresta (reveal) entre as frentes das gavetas, em mm." },
        { id: "slide_key", p: "slide_key", l: "Corrediça", t: "slide",
          h: "Modelo de corrediça da biblioteca de ferragens. Define quanto espaço a corrediça " +
             "ocupa (lateral: roldana/telescópica; oculta: sob a gaveta), a profundidade da " +
             "caixa e a profundidade mínima do armário." },
        { id: "slideInfo", t: "note" },
        { id: "slide_custom", p: "slide.custom", l: "Personalizar medidas da corrediça",
          t: "bool",
          h: "Ligado: as cinco medidas abaixo valem para este armário e sobrepõem a biblioteca " +
             "de ferragens. Desligado: apenas mostram as medidas da corrediça escolhida." },
        { id: "slide_side_space", p: "slide.side_space", l: "Espaço lateral (por lado)",
          t: "num", u: "mm", step: 0.1, min: 0,
          h: "Espaço que a corrediça ocupa de CADA lado, entre a lateral do móvel e a lateral " +
             "externa da caixa. Lateral: roldana ~12,5 mm, telescópica 45 mm ~12,7 mm. Oculta: " +
             "~6,5 mm. A largura da caixa = vão livre − 2× este valor." },
        { id: "slide_bottom_clearance", p: "slide.bottom_clearance", l: "Folga sob a caixa",
          t: "num", u: "mm", step: 0.5, min: 0,
          h: "Folga entre o piso do vão e o fundo da caixa, em mm. A corrediça oculta trabalha " +
             "nesse espaço (~13 mm); na montagem lateral basta ~3 mm." },
        { id: "slide_back_clearance", p: "slide.back_clearance", l: "Folga no fundo (caixa↔fundo)",
          t: "num", u: "mm", step: 0.5, min: 0,
          h: "Espaço deixado entre a traseira da caixa da gaveta e o fundo do armário, em mm." },
        { id: "slide_box_depth", p: "slide.box_depth", l: "Profundidade da caixa", t: "num",
          u: "mm", step: 10, min: 0,
          h: "Profundidade (comprimento) da caixa da gaveta, em mm — normalmente igual ao " +
             "comprimento nominal da corrediça." },
        { id: "slide_min_cabinet_depth", p: "slide.min_cabinet_depth",
          l: "Profundidade mín. do armário", t: "num", u: "mm", step: 10, min: 0,
          h: "Profundidade mínima que o armário precisa ter para esta corrediça, em mm. Usada " +
             "na validação antes de gerar." },
        { id: "insert_real_hardware", p: "insert_real_hardware", l: "Inserir modelo 3D",
          t: "bool",
          h: "Ligado: importa o CAD real da corrediça. Desligado: usa uma caixa proxy leve " +
             "(recomendado com muitas gavetas)." },
        { id: "drawer_box_material", p: "drawer.box_material", l: "Material da caixa",
          t: "material", h: "Chapa das laterais/travessas da caixa da gaveta." },
        { id: "drawer_box_t", p: "drawer.box_t", l: "Espessura da caixa", t: "num", u: "mm",
          step: 1,
          h: "Grossura da chapa da caixa da gaveta (padrão 16 mm, previsto pela corrediça " +
             "UM A30)." },
        { id: "drawer_bottom_material", p: "drawer.bottom_material", l: "Material do fundo",
          t: "material",
          h: "Chapa do fundo da gaveta (fina, encaixada em rasgo nas quatro paredes)." },
        { id: "drawer_bottom_t", p: "drawer.bottom_t", l: "Espessura do fundo", t: "num",
          u: "mm", step: 1, h: "Grossura do fundo da gaveta, em mm (~6 mm)." },
        { id: "drawer_face_material", p: "drawer.face_material", l: "Material da frente",
          t: "material", h: "Chapa da frente da gaveta (fitada nos quatro lados)." },
        { id: "drawer_face_t", p: "drawer.face_t", l: "Espessura da frente", t: "num", u: "mm",
          step: 1, h: "Grossura da frente da gaveta, em mm (~18 mm)." }
      ]
    },

    /* --------------------------------------------------------------- hinge -- */
    {
      key: "hinge", title: "Dobradiça (avançado)", tag: "— furação de caneco",
      diagram: "hinge",
      hint: "Valores da furação de dobradiça de caneco oculta. Os padrões seguem a prática de " +
            "marcenaria; ajuste só se souber o que está fazendo.",
      fields: [
        { id: "hinge_cup_diameter", p: "hinge.cup_diameter", l: "Diâmetro do caneco", t: "num",
          u: "mm", step: 0.5, h: "Diâmetro do furo do caneco na porta (padrão 35 mm)." },
        { id: "hinge_cup_depth", p: "hinge.cup_depth", l: "Profund. do caneco", t: "num",
          u: "mm", step: 0.5, h: "Profundidade do furo cego do caneco, em mm." },
        { id: "hinge_cup_edge", p: "hinge.cup_edge", l: "Borda até o centro", t: "num",
          u: "mm", step: 0.5, h: "Distância da borda da porta ao centro do caneco, em mm." },
        { id: "hinge_end_inset", p: "hinge.end_inset", l: "Recuo das pontas", t: "num",
          u: "mm", step: 5,
          h: "Distância das pontas da porta à primeira/última dobradiça, em mm." },
        { id: "hinge_shelf_clearance", p: "hinge.shelf_clearance", l: "Folga da prateleira",
          t: "num", u: "mm", step: 1,
          h: "Distância mínima entre uma dobradiça e uma prateleira; conflitos são " +
             "reposicionados automaticamente." }
      ]
    },

    /* ----------------------------------------------------------------- tol -- */
    {
      key: "tol", title: "Tolerâncias (avançado)", tag: "— folgas de encaixe",
      diagram: "tol",
      hint: "Valores finos de encaixe. Os padrões seguem a prática de marcenaria; ajuste só " +
            "se souber o que está fazendo.",
      fields: [
        { id: "tol_dado_bottom_clearance", p: "tol.dado_bottom_clearance",
          l: "Folga fundo da ranhura", t: "num", u: "mm", step: 0.1,
          h: "Folga extra no fundo do rasgo (dado) para o painel encaixar sem forçar, em mm." },
        { id: "tol_dado_side_clearance", p: "tol.dado_side_clearance",
          l: "Folga lateral da ranhura", t: "num", u: "mm", step: 0.1,
          h: "Folga extra nas laterais do rasgo (largura da ranhura vs. espessura do painel), " +
             "em mm." },
        { id: "tol_shelf_back_gap", p: "tol.shelf_back_gap", l: "Folga prateleira-fundo",
          t: "num", u: "mm", step: 0.5,
          h: "Espaço deixado entre a traseira da prateleira e o painel de fundo, em mm." },
        { id: "tol_shelf_front_setback", p: "tol.shelf_front_setback", l: "Recuo da prateleira",
          t: "num", u: "mm", step: 1,
          h: "Recuo base da prateleira em relação à frente do corpo, em mm (quando não " +
             "alinhada à frente)." },
        { id: "tol_shelf_door_clearance", p: "tol.shelf_door_clearance",
          l: "Folga prateleira-porta", t: "num", u: "mm", step: 0.5,
          h: "Folga entre a prateleira e uma porta fechada, para a porta não encostar, em mm." }
      ]
    }
  ];

  /* ===========================================================================
     REFERENCE DIAGRAMS — one schematic per section, with dimension callouts, so
     every measurement field has a picture of what it refers to. Static and
     illustrative (the Fixação lateral drawing below is the live/editable one).
  =========================================================================== */
  function _ah(x, y, d) {                     // arrowhead, tip at (x,y): l/r/u/d
    var s = 5, w = 2.6;
    if (d === "l") return '<polygon class="ar" points="' + x + ',' + y + ' ' + (x + s) + ',' + (y - w) + ' ' + (x + s) + ',' + (y + w) + '"/>';
    if (d === "r") return '<polygon class="ar" points="' + x + ',' + y + ' ' + (x - s) + ',' + (y - w) + ' ' + (x - s) + ',' + (y + w) + '"/>';
    if (d === "u") return '<polygon class="ar" points="' + x + ',' + y + ' ' + (x - w) + ',' + (y + s) + ' ' + (x + w) + ',' + (y + s) + '"/>';
    return '<polygon class="ar" points="' + x + ',' + y + ' ' + (x - w) + ',' + (y - s) + ' ' + (x + w) + ',' + (y - s) + '"/>';
  }
  function _dimH(x0, x1, y, t) {               // horizontal dimension line
    return '<line class="dim" x1="' + x0 + '" y1="' + y + '" x2="' + x1 + '" y2="' + y + '"/>'
         + _ah(x0, y, "l") + _ah(x1, y, "r")
         + '<text class="lbl" x="' + ((x0 + x1) / 2) + '" y="' + (y - 4) + '">' + t + '</text>';
  }
  function _dimV(x, y0, y1, t) {               // vertical dimension line
    var my = (y0 + y1) / 2;
    return '<line class="dim" x1="' + x + '" y1="' + y0 + '" x2="' + x + '" y2="' + y1 + '"/>'
         + _ah(x, y0, "u") + _ah(x, y1, "d")
         + '<text class="lbl" x="' + x + '" y="' + my + '" transform="rotate(-90 ' + x + ' ' + my + ')">' + t + '</text>';
  }
  function _lead(x0, y0, x1, y1, t, tx, ty) {  // leader line + free label
    return '<line class="dim" x1="' + x0 + '" y1="' + y0 + '" x2="' + x1 + '" y2="' + y1 + '"/>'
         + _ah(x1, y1, (Math.abs(x1 - x0) > Math.abs(y1 - y0)) ? (x1 > x0 ? "r" : "l") : (y1 > y0 ? "d" : "u"))
         + '<text class="lbl" x="' + tx + '" y="' + ty + '">' + t + '</text>';
  }

  var DIAGRAMS = {
    /* Overall carcass box: W × H × D and carcass thickness t. */
    dims: { vb: "0 0 260 215", cap:
      "L largura · A altura · P profundidade · espessura = chapa da carcaça (laterais, base e tampo).",
      svg:
        '<polygon class="pnl2" points="40,64 76,36 226,36 190,64"/>'
      + '<polygon class="pnl2" points="190,64 226,36 226,156 190,184"/>'
      + '<rect class="pnl"  x="40"  y="64" width="150" height="120"/>'
      + '<rect class="open" x="52"  y="76" width="126" height="96"/>'
      + _dimH(40, 190, 202, "L")
      + _dimV(26, 64, 184, "A")
      + '<line class="dim" x1="190" y1="64" x2="226" y2="36"/>'
      + '<text class="lbl" x="233" y="40">P</text>'
      + '<line class="dim" x1="76" y1="64" x2="76" y2="76"/>'
      + '<line class="dim" x1="71" y1="64" x2="81" y2="64"/>'
      + '<line class="dim" x1="71" y1="76" x2="81" y2="76"/>'
      + '<text class="lbl" x="99" y="73">esp.</text>'
    },

    /* Panel face with the four taped edges (C1/C2 long, L1/L2 short). */
    fita: { vb: "0 0 260 172", cap:
      "C1/C2 = bordas de comprimento (as maiores) · L1/L2 = bordas de largura (as menores). "
      + "Fita fina = 0,4 mm · fita grossa = 1,0 mm.",
      svg:
        '<rect class="pnl" x="60" y="44" width="140" height="84"/>'
      + '<rect class="tape"  x="60"  y="44"  width="140" height="5"/>'
      + '<rect class="tape"  x="60"  y="123" width="140" height="5"/>'
      + '<rect class="tape2" x="60"  y="44"  width="5"   height="84"/>'
      + '<rect class="tape2" x="195" y="44"  width="5"   height="84"/>'
      + '<text class="lbl" x="130" y="38">C1</text>'
      + '<text class="lbl" x="130" y="146">C2</text>'
      + '<text class="lbl" x="50"  y="86" transform="rotate(-90 50 86)">L1</text>'
      + '<text class="lbl" x="210" y="86" transform="rotate(-90 210 86)">L2</text>'
    },

    /* Rear-corner detail — GROOVE (encaixado): back seated in a dado. */
    back: { vb: "0 0 260 180", cap:
      "Encaixado: o fundo assenta numa ranhura (dado) nas laterais/base/tampo. "
      + "Espessura do fundo, profundidade da ranhura e recuo do fundo.",
      svg:
        '<rect class="pnl"  x="60"  y="28"  width="16"  height="122"/>'
      + '<rect class="pnl2" x="68"  y="116" width="142" height="14"/>'
      + '<rect class="pnl2" x="68"  y="116" width="8"   height="14"/>'
      + '<text class="lbl-m" x="45" y="45" transform="rotate(-90 45 45)">lateral</text>'
      + '<text class="lbl-m" x="150" y="112">fundo</text>'
      + _dimV(228, 116, 150, "recuo")
      + '<line class="dim" x1="210" y1="116" x2="228" y2="116"/>'
      + '<line class="dim" x1="210" y1="150" x2="228" y2="150"/>'
      + _lead(150, 168, 150, 131, "esp. fundo", 150, 166)
      + _lead(96, 100, 72, 116, "ranhura", 100, 96)
    },

    /* Rear-corner detail — OVERLAY (sobreposto): back applied to the rear face. */
    back_overlay: { vb: "0 0 260 180", cap:
      "Sobreposto: o fundo é aplicado (aparafusado) na face traseira do corpo, em "
      + "toda a largura, encostado nas bordas traseiras e sem ranhura. Só a espessura importa.",
      svg:
        '<rect class="pnl"  x="60"  y="30"  width="16"  height="104"/>'
      + '<rect class="pnl2" x="60"  y="134" width="150" height="16"/>'
      + '<text class="lbl-m" x="45" y="48" transform="rotate(-90 45 48)">lateral</text>'
      + '<text class="lbl-m" x="150" y="146">fundo</text>'
      + _lead(96, 112, 70, 134, "aplicado atrás", 100, 108)
      + _dimV(226, 134, 150, "esp.")
      + '<line class="dim" x1="210" y1="134" x2="226" y2="134"/>'
      + '<line class="dim" x1="210" y1="150" x2="226" y2="150"/>'
    },

    /* Side elevation: toe kick under the carcass (front to the right). */
    kick: { vb: "0 0 260 200", cap:
      "Altura e recuo (da frente do armário) do rodapé, e espessura da tábua frontal. "
      + "Vão máx. sem reforço limita o comprimento antes de reforçar.",
      svg:
        '<rect class="pnl"  x="40" y="40"  width="180" height="70"/>'
      + '<rect class="pnl2" x="184" y="110" width="10" height="60"/>'
      + '<line class="floor" x1="26" y1="170" x2="234" y2="170"/>'
      + '<text class="lbl-m" x="120" y="80">armário</text>'
      + '<text class="lbl-m" x="55"  y="184">piso</text>'
      + _dimV(240, 110, 170, "altura")
      + _dimH(194, 220, 184, "recuo")
      + _lead(189, 124, 189, 112, "esp.", 189, 120)
    },

    /* Front elevation: applied finishing panels OUTSIDE the carcass + top view. */
    tamponamento: { vb: "0 0 260 280", cap:
      "Painéis aplicados por fora do corpo: cada lado ligado aumenta a largura em uma "
      + "espessura e o superior aumenta a altura. As laterais vão do piso ao topo; o "
      + "superior cobre toda a largura acabada. Avanço frontal = quanto o painel avança "
      + "à frente (0 = faceado); a traseira fica sempre alinhada com o fundo.",
      svg:
        '<rect class="pnl"  x="62"  y="46" width="136" height="122"/>'
      + '<rect class="pnl2" x="50"  y="46" width="12"  height="122"/>'
      + '<rect class="pnl2" x="198" y="46" width="12"  height="122"/>'
      + '<rect class="pnl2" x="50"  y="34" width="160" height="12"/>'
      + '<line class="floor" x1="26" y1="168" x2="234" y2="168"/>'
      + '<text class="lbl-m" x="130" y="28">tamponamento superior</text>'
      + '<text class="lbl-m" x="130" y="112">corpo</text>'
      + '<text class="lbl-m" x="42" y="112" transform="rotate(-90 42 112)">tamponamento</text>'
      + '<line class="dim" x1="50" y1="170" x2="50" y2="186"/>'
      + '<line class="dim" x1="62" y1="170" x2="62" y2="186"/>'
      + _dimH(50, 62, 186, "esp.")
      + '<line class="dim" x1="212" y1="34" x2="234" y2="34"/>'
      + '<line class="dim" x1="212" y1="46" x2="234" y2="46"/>'
      + _dimV(234, 34, 46, "esp.")
      + '<text class="lbl-m" x="130" y="201">vista de cima — avanço frontal</text>'
      + '<line class="dim" x1="74" y1="222" x2="74" y2="250"/>'
      + '<line class="dim" x1="92" y1="222" x2="92" y2="228"/>'
      + _dimH(74, 92, 222, "avanço")
      + '<rect class="pnl"  x="92" y="228" width="118" height="22"/>'
      + '<rect class="pnl2" x="74" y="250" width="136" height="10"/>'
      + '<text class="lbl-m" x="151" y="242">corpo</text>'
      + '<text class="lbl-m" x="64" y="239" transform="rotate(-90 64 239)">frente</text>'
      + '<text class="lbl-m" x="140" y="274">traseira alinhada com o fundo</text>'
    },

    /* Front elevation: top valance (sanefa) + side scribe strips + U top view. */
    arremate: { vb: "0 0 260 304", cap:
      "Sanefa: peça frontal do topo do armário até o teto — altura = folga até o teto "
      + "(ou altura do teto − A). Réguas: peças frontais laterais com a largura da folga "
      + "até a parede, do piso ao teto. Sanefa em U acrescenta um retorno de profundidade "
      + "total sobre cada lateral. Todas cortadas com sobra e ajustadas no local.",
      svg:
        '<line class="floor" x1="24" y1="30"  x2="236" y2="30"/>'
      + '<line class="floor" x1="24" y1="210" x2="236" y2="210"/>'
      + '<line class="floor" x1="44"  y1="22" x2="44"  y2="218"/>'
      + '<line class="floor" x1="216" y1="22" x2="216" y2="218"/>'
      + '<rect class="pnl"  x="62"  y="74" width="136" height="136"/>'
      + '<rect class="pnl2" x="62"  y="30" width="136" height="44"/>'
      + '<rect class="pnl2" x="44"  y="30" width="18"  height="180"/>'
      + '<rect class="pnl2" x="198" y="30" width="18"  height="180"/>'
      + '<text class="lbl-m" x="32"  y="24">teto</text>'
      + '<text class="lbl-m" x="200" y="224">piso</text>'
      + '<text class="lbl-m" x="100" y="58">sanefa</text>'
      + '<text class="lbl-m" x="130" y="145">armário</text>'
      + '<text class="lbl-m" x="53"  y="140" transform="rotate(-90 53 140)">régua</text>'
      + '<text class="lbl-m" x="207" y="140" transform="rotate(-90 207 140)">régua</text>'
      + '<text class="lbl-m" x="32"  y="120" transform="rotate(-90 32 120)">parede</text>'
      + '<text class="lbl-m" x="228" y="120" transform="rotate(-90 228 120)">parede</text>'
      + _dimV(162, 30, 74, "folga teto")
      + '<line class="dim" x1="44" y1="212" x2="44" y2="226"/>'
      + '<line class="dim" x1="62" y1="212" x2="62" y2="226"/>'
      + _dimH(44, 62, 226, "folga parede")
      + '<text class="lbl-m" x="130" y="252">sanefa em U — vista de cima</text>'
      + '<rect class="open" x="76"  y="262" width="108" height="34" stroke-dasharray="4 3"/>'
      + '<rect class="pnl2" x="76"  y="262" width="10"  height="34"/>'
      + '<rect class="pnl2" x="174" y="262" width="10"  height="34"/>'
      + '<rect class="pnl2" x="86"  y="288" width="88"  height="8"/>'
      + '<text class="lbl-m" x="70"  y="279" transform="rotate(-90 70 279)">retorno</text>'
      + '<text class="lbl-m" x="190" y="279" transform="rotate(-90 190 279)">retorno</text>'
      + '<text class="lbl-m" x="130" y="282">frente</text>'
      + _lead(206, 272, 170, 292, "esp.", 210, 268)
    },

    /* Front elevation: two overlay doors with the reveal gap. */
    doors: { vb: "0 0 260 200", cap:
      "N portas iguais · folga = reveal entre as portas e nas bordas · espessura da porta. "
      + "Sobreposta cobre a frente; embutida assenta no vão.",
      svg:
        '<rect class="pnl"  x="40" y="24" width="180" height="150"/>'
      + '<rect class="pnl2" x="46" y="30" width="82"  height="138"/>'
      + '<rect class="pnl2" x="132" y="30" width="82" height="138"/>'
      + _lead(130, 10, 130, 30, "folga", 130, 8)
      + _lead(236, 100, 214, 100, "folga", 244, 103)
      + '<text class="lbl-m" x="87"  y="103">porta</text>'
      + '<text class="lbl-m" x="173" y="103">porta</text>'
    },

    /* Front elevation of a 3-drawer stack + the box hidden behind a face. */
    drawers: { vb: "0 0 260 200", cap:
      "N gavetas empilhadas · folga = reveal entre as frentes · espessuras da frente, "
      + "da caixa e do fundo da gaveta. A corrediça ocupa um espaço de cada lado "
      + "(lateral ~12,5–12,7 mm · oculta ~6,5 mm): caixa = vão livre − 2× esse espaço.",
      svg:
        '<rect class="pnl"  x="40" y="20" width="180" height="160"/>'
      + '<rect class="pnl2" x="46" y="26" width="168" height="46"/>'
      + '<rect class="pnl2" x="46" y="76" width="168" height="46"/>'
      + '<rect class="pnl2" x="46" y="126" width="168" height="46"/>'
      + '<rect x="70" y="82" width="120" height="34" fill="none" stroke="var(--accent)" stroke-width="1" stroke-dasharray="4 3"/>'
      + '<text class="lbl-m" x="130" y="102">caixa</text>'
      + _dimH(46, 70, 119, "corrediça")
      + _lead(232, 97, 214, 97, "folga", 240, 100)
    },

    /* Door back face with concealed-hinge cup bores + an edge section. */
    hinge: { vb: "0 0 260 200", cap:
      "Caneco: diâmetro (Ø) e profundidade · distância borda→centro · recuo das pontas. "
      + "O nº de dobradiças cresce com a altura da porta.",
      svg:
        '<rect class="pnl" x="70" y="20" width="120" height="160"/>'
      + '<circle class="pnl2" cx="100" cy="60"  r="14"/>'
      + '<circle class="pnl2" cx="100" cy="140" r="14"/>'
      + _dimH(86, 114, 50, "Ø")
      + _dimH(70, 100, 192, "borda→centro")
      + _dimV(54, 20, 60, "recuo")
      + '<rect class="pnl" x="212" y="44" width="20" height="90"/>'
      + '<rect class="open" x="212" y="80" width="12" height="18"/>'
      + _dimH(212, 224, 110, "prof.")
      + '<text class="lbl-m" x="130" y="16">face interna da porta</text>'
    },

    /* Side section: a shelf between the back panel and a closed door. */
    tol: { vb: "0 0 260 195", cap:
      "Recuo da prateleira (frente), folga prat.–fundo (atrás) e folga prat.–porta. "
      + "Folgas da ranhura (fundo/lateral) afinam o encaixe do fundo.",
      svg:
        '<rect class="pnl"  x="30"  y="24" width="200" height="150"/>'
      + '<rect class="pnl2" x="38"  y="24" width="10"  height="150"/>'
      + '<rect class="pnl2" x="212" y="30" width="10"  height="138"/>'
      + '<rect class="open" x="64"  y="94" width="132" height="12"/>'
      + '<text class="lbl-m" x="43"  y="120" transform="rotate(-90 43 120)">fundo</text>'
      + '<text class="lbl-m" x="130" y="88">prateleira</text>'
      + '<text class="lbl-m" x="217" y="120" transform="rotate(-90 217 120)">porta</text>'
      + _dimH(48, 64, 130, "atrás")
      + _dimH(196, 212, 130, "frente")
    }
  };

  function buildDiagramHTML(name) {
    var d = DIAGRAMS[name];
    if (!d) return "";
    var s = '<div class="dg-wrap"><svg class="dg-svg" viewBox="' + d.vb + '" '
          + 'xmlns="http://www.w3.org/2000/svg">' + d.svg + '</svg></div>';
    if (d.cap) s += '<p class="dg-cap">' + d.cap + '</p>';
    return s;
  }

  /* Live, editable Fixação lateral schematic (front elevation): the two overhang
     numbers are inputs overlaid on the drawing itself. */
  function joineryDiagramHTML() {
    return '<div class="jn-wrap">'
      + '<svg class="jn-svg" id="jnSvg" viewBox="0 0 240 300" xmlns="http://www.w3.org/2000/svg">'
      +   '<rect id="jnTop"   class="jn-panel" x="46" y="18"  width="148" height="20"/>'
      +   '<rect id="jnBase"  class="jn-panel" x="46" y="216" width="148" height="20"/>'
      +   '<rect id="jnKick"  class="jn-kick"  x="30" y="236" width="180" height="46"/>'
      +   '<rect id="jnSideL" class="jn-side"  x="30"  y="18" width="16" height="218"/>'
      +   '<rect id="jnSideR" class="jn-side"  x="194" y="18" width="16" height="218"/>'
      +   '<line id="jnFloor" class="jn-floor" x1="14" y1="284" x2="226" y2="284"/>'
      +   '<text class="jn-lbl" x="120" y="33">Tampo</text>'
      +   '<text class="jn-lbl" x="120" y="231">Base</text>'
      +   '<text id="jnKickLbl" class="jn-lbl" x="120" y="264">Rodapé</text>'
      + '</svg>'
      + '<div class="jn-oh" id="jnTopOhBox" style="left:15%; top:12.5%;">'
      +   '<input type="number" id="joineryTopOverhang" step="1" min="0" '
      +   'title="Tampo: avanço da lateral"><span class="unit">mm</span></div>'
      + '<div class="jn-oh" id="jnBotOhBox" style="left:15%; top:75%;">'
      +   '<input type="number" id="joineryBottomOverhang" step="1" min="0" '
      +   'title="Base: avanço da lateral"><span class="unit">mm</span></div>'
      + '</div>';
  }

  /* Redraw the Fixação lateral schematic from the current form values, and dim
     the overhang boxes at junctions where they do not apply. */
  function renderJoineryDiagram() {
    var svg = byId("jnSvg"); if (!svg) return;
    var t = num("t", 18) || 18;
    var kickEl = byId("with_toe_kick");
    var hasKick = kickEl ? !!kickEl.checked : true;
    var bMode = val("joineryBottomMode", "aligned");
    var tMode = val("joineryTopMode", "aligned");
    var bOh = num("joineryBottomOverhang", 0);
    var tOh = num("joineryTopOverhang", 0);
    var s2fEl = byId("joinerySidesToFloor");
    var s2f = !!(s2fEl && s2fEl.checked) && hasKick;
    var X0 = 30, X1 = 210, SW = 16, TH = 20;         // frame + panel thickness (svg units)
    var TF = 18, topBottomY = TF + TH;               // top panel: face y .. bottom y
    var baseBotY = 236, baseTopY = baseBotY - TH;    // base panel top/bottom y
    var floorY = 284, kickTopY = baseBotY;           // toe-kick zone
    function travel(oh) { return Math.max(0, Math.min(oh / t, 1.8)) * TH; }
    var topX = (tMode === "over") ? X0 : (X0 + SW);
    var topW = (tMode === "over") ? (X1 - X0) : (X1 - X0 - 2 * SW);
    var baseX = (bMode === "over") ? X0 : (X0 + SW);
    var baseW = (bMode === "over") ? (X1 - X0) : (X1 - X0 - 2 * SW);
    var sideTopY = (tMode === "over") ? (topBottomY - travel(tOh)) : TF;
    var sideBotY = s2f ? floorY : ((bMode === "over") ? (baseTopY + travel(bOh)) : baseBotY);
    function setR(id, x, y, w, h) {
      var r = byId(id); if (!r) return;
      r.setAttribute("x", x); r.setAttribute("y", y);
      r.setAttribute("width", Math.max(0, w)); r.setAttribute("height", Math.max(0, h));
    }
    function showEl(id, on) { var e = byId(id); if (e) e.style.display = on ? "" : "none"; }
    setR("jnTop", topX, TF, topW, TH);
    setR("jnBase", baseX, baseTopY, baseW, TH);
    setR("jnSideL", X0, sideTopY, SW, sideBotY - sideTopY);
    setR("jnSideR", X1 - SW, sideTopY, SW, sideBotY - sideTopY);
    showEl("jnKick", hasKick); showEl("jnFloor", hasKick); showEl("jnKickLbl", hasKick);
    if (hasKick) {
      var kx = s2f ? (X0 + SW) : X0, kw = s2f ? (X1 - X0 - 2 * SW) : (X1 - X0);
      setR("jnKick", kx, kickTopY, kw, floorY - kickTopY);
    }
    var tb = byId("jnTopOhBox"); if (tb) tb.classList.toggle("off", tMode !== "over");
    var bb = byId("jnBotOhBox"); if (bb) bb.classList.toggle("off", bMode !== "over");
    if (s2fEl) s2fEl.disabled = !hasKick;
  }

  /* ===========================================================================
     RENDERING — the same DOM on both pages, built from SPEC
  =========================================================================== */
  function byId(id) { return document.getElementById(id); }
  function val(id, dflt) { var e = byId(id); return (e && e.value != null && e.value !== "") ? e.value : dflt; }
  function num(id, dflt) { var e = byId(id); var v = e ? parseFloat(e.value) : NaN; return isNaN(v) ? dflt : v; }

  function el(tag, attrs, kids) {
    var e = document.createElement(tag);
    if (attrs) for (var k in attrs) { if (k === "class") e.className = attrs[k]; else e.setAttribute(k, attrs[k]); }
    (kids || []).forEach(function (c) {
      e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return e;
  }
  function makeInfo(tip) {
    return el("span", { class: "info", tabindex: "0", role: "note",
                        "aria-label": tip, "data-tip": tip }, ["i"]);
  }
  function makeSelect(id, pairs) {           // pairs: [[value,label], …]
    var s = el("select", { id: id, class: "grow" });
    (pairs || []).forEach(function (p) {
      var o = el("option");
      o.value = (p instanceof Array) ? p[0] : p;
      o.textContent = (p instanceof Array) ? p[1] : p;
      s.appendChild(o);
    });
    return s;
  }
  function rowWith(f, control) {
    var lab = el("label", {}, [f.l || ""]);
    if (f.h) lab.appendChild(makeInfo(f.h));
    var row = el("div", { class: "row" }, [lab, control]);
    if (f.rowId) row.id = f.rowId;
    return row;
  }

  function isFor(f, surface) { return !f.only || f.only === surface; }

  /* One field -> one DOM row (or a <p> for a "note" placeholder). */
  function buildField(f) {
    if (f.t === "note") return el("p", { class: "hint", id: f.id, style: "display:none" });
    if (f.t === "bool") {
      var cb = el("input", { type: "checkbox", id: f.id });
      var sw = el("span", { class: "sw" }, [cb, el("span", { class: "track" })]);
      return rowWith(f, sw);
    }
    if (f.t === "sel" || f.t === "material" || f.t === "material_inherit"
        || f.t === "slide" || f.t === "fita") {
      // Options are filled by refreshLists(); "sel" has fixed options right away.
      return rowWith(f, makeSelect(f.id, f.t === "sel" ? f.opts : []));
    }
    if (f.t === "text") {
      return rowWith(f, el("input", { type: "text", id: f.id, class: "grow" }));
    }
    // num / int
    var attrs = { type: "number", id: f.id, step: (f.t === "int" ? "1" : (f.step != null ? String(f.step) : "any")) };
    if (f.min != null) attrs.min = String(f.min);
    var input = el("input", attrs);
    var fld = el("div", { class: "field" + (f.u ? " has-unit" : "") }, [input]);
    if (f.u) fld.appendChild(el("span", { class: "unit" }, [f.u]));
    return rowWith(f, fld);
  }

  function chevron() {
    var wrap = el("span");
    wrap.innerHTML = '<svg class="chev" viewBox="0 0 24 24" width="16" height="16" fill="none" '
                   + 'stroke="currentColor" stroke-width="2"><path d="M9 6l6 6-6 6"/></svg>';
    return wrap.firstChild;
  }

  /* Body of one section: hint -> diagram -> rows -> surface note. */
  function buildSectionBody(sec, surface, noDiagram) {
    var body = el("div", { class: "sbody" });
    if (sec.hint) { var h = el("p", { class: "hint" }); h.innerHTML = sec.hint; body.appendChild(h); }
    if (noDiagram) { /* the host page draws its own illustration for this section */ }
    else if (sec.diagram === "joinery_live") {
      var jn = el("div"); jn.innerHTML = joineryDiagramHTML();
      body.appendChild(jn.firstChild);
    } else if (sec.diagram) {
      var dg = el("div", { class: "dg-anchor", "data-diagram": sec.diagram });
      dg.innerHTML = buildDiagramHTML(sec.diagram);
      body.appendChild(dg);
    }
    sec.fields.forEach(function (f) {
      if (f.inDiagram || !isFor(f, surface)) return;   // diagram-hosted / other surface
      body.appendChild(buildField(f));
    });
    var note = sec.notes && sec.notes[surface];
    if (note) body.appendChild(el("p", { class: "hint", style: "margin-top:10px" }, [note]));
    return body;
  }

  /* Render sections into `host`.
       opts.surface   'prefs' | 'cabinet'                 (required)
       opts.sections  array of section keys (default: all)
       opts.plain     true = emit only the rows, no collapsible wrapper
       opts.noDiagram true = skip the section schematic (the page draws its own)
       opts.open      array of section keys to start expanded (default: sec.open) */
  function render(host, opts) {
    opts = opts || {};
    var surface = opts.surface || "prefs";
    var keys = opts.sections || SPEC.map(function (s) { return s.key; });
    var openKeys = opts.open || null;
    host.innerHTML = "";
    keys.forEach(function (key) {
      var sec = sectionByKey(key); if (!sec) return;
      var body = buildSectionBody(sec, surface, !!opts.noDiagram);
      if (opts.plain) {                       // rows only (hosted in a page card)
        while (body.firstChild) host.appendChild(body.firstChild);
        return;
      }
      var head = el("button", { class: "shead", type: "button", "data-toggle": sectionDomId(key) },
                    [el("span", {}, [sec.title])]);
      if (sec.tag) head.appendChild(el("span", { class: "tag" }, [sec.tag]));
      head.appendChild(chevron());
      var isOpen = openKeys ? (openKeys.indexOf(key) >= 0) : !!sec.open;
      var section = el("div", { class: "section" + (isOpen ? " open" : ""), id: sectionDomId(key) },
                       [head, body]);
      head.addEventListener("click", function () { section.classList.toggle("open"); });
      host.appendChild(section);
    });
  }

  function sectionDomId(key) { return "sec_" + key; }
  function sectionByKey(key) {
    for (var i = 0; i < SPEC.length; i++) if (SPEC[i].key === key) return SPEC[i];
    return null;
  }
  /* DOM ids of the collapsible sections rendered for a key list (advanced gate). */
  function sectionDomIds(keys) {
    return (keys || SPEC.map(function (s) { return s.key; })).map(sectionDomId);
  }
  /* Every field of the given sections that applies to `surface`, diagram-hosted
     controls included (they are read/written like any other field). */
  function fieldsFor(surface, keys) {
    var out = [];
    (keys || SPEC.map(function (s) { return s.key; })).forEach(function (key) {
      var sec = sectionByKey(key); if (!sec) return;
      sec.fields.forEach(function (f) {
        if (f.t === "note" || !f.p || !isFor(f, surface)) return;
        out.push(f);
      });
    });
    return out;
  }

  /* ===========================================================================
     OPTION LISTS / FORM <-> CFG
     ctx: { surface, sections, materials:[names], slides:[specs],
            fitaChoices:[{value,label}], onChange, onInput }
  =========================================================================== */
  function fitaPairs(ctx) {
    var fc = ctx && ctx.fitaChoices;
    if (fc && fc.length) return fc.map(function (c) { return [c.value, c.label]; });
    return FITA_CHOICES;
  }
  function materialPairs(ctx, current, inherit) {
    var names = (ctx && ctx.materials ? ctx.materials : []).slice();
    if (current && current !== "" && names.indexOf(current) < 0) names.unshift(current);
    var pairs = names.map(function (n) { return [n, n]; });
    if (inherit) pairs.unshift(["", INHERIT_LABEL]);
    return pairs;
  }

  /* Refill every option-bearing select, preserving the current selection. */
  function refreshLists(ctx) {
    fieldsFor(ctx.surface, ctx.sections).forEach(function (f) {
      var e = byId(f.id); if (!e || e.tagName !== "SELECT") return;
      var cur = e.value;
      if (f.t === "material" || f.t === "material_inherit") {
        setOptions(e, materialPairs(ctx, cur, f.t === "material_inherit"));
      } else if (f.t === "slide") {
        setOptions(e, (ctx.slides || []).map(function (s) { return [s.key, s.desc]; }));
      } else if (f.t === "fita") {
        setOptions(e, fitaPairs(ctx));
      } else if (f.t === "sel") {
        setOptions(e, f.opts || []);
      } else { return; }
      e.value = cur;
      if (!e.value && e.options.length) e.selectedIndex = 0;
    });
  }
  function setOptions(sel, pairs) {
    sel.innerHTML = "";
    pairs.forEach(function (p) {
      var o = document.createElement("option");
      o.value = p[0]; o.textContent = p[1];
      sel.appendChild(o);
    });
  }

  /* cfg -> form. Fills option lists first so every value can be selected. */
  function writeForm(cfg, ctx) {
    refreshLists(ctx);
    fieldsFor(ctx.surface, ctx.sections).forEach(function (f) {
      var e = byId(f.id); if (!e) return;
      var v = getPath(cfg, f.p);
      if (f.t === "bool") { e.checked = !!v; return; }
      if (f.t === "material" || f.t === "material_inherit") {
        var name = (v == null) ? "" : String(v);
        if (name && !hasOption(e, name)) e.insertBefore(newOption(name, name), e.firstChild);
        e.value = name;
        if (!hasOption(e, name) && e.options.length) e.selectedIndex = 0;
        return;
      }
      e.value = (v == null) ? "" : v;
      if (e.tagName === "SELECT" && !hasOption(e, String(v)) && e.options.length) e.selectedIndex = 0;
    });
    syncDynamics(ctx);
  }
  function hasOption(sel, v) {
    for (var i = 0; i < sel.options.length; i++) if (sel.options[i].value === v) return true;
    return false;
  }
  function newOption(v, l) { var o = document.createElement("option"); o.value = v; o.textContent = l; return o; }

  /* form -> cfg. Blank/NaN numbers keep the stored value rather than zeroing it. */
  function readForm(cfg, ctx) {
    fieldsFor(ctx.surface, ctx.sections).forEach(function (f) {
      var e = byId(f.id); if (!e) return;
      var v;
      if (f.t === "bool") v = e.checked;
      else if (f.t === "int") { v = parseInt(e.value, 10); if (isNaN(v)) return; }
      else if (f.t === "num") { v = parseFloat(e.value); if (isNaN(v)) return; }
      else v = e.value;                       // text / sel / material / slide / fita
      setPath(cfg, f.p, v);
    });
    return cfg;
  }

  /* ===========================================================================
     DYNAMIC BEHAVIOUR — identical on both pages
  =========================================================================== */
  /* Back panel: swap the schematic and hide the groove-only rows in overlay mode. */
  function renderBackMode() {
    var e = byId("back_mode"); if (!e) return;
    var overlay = (e.value === "overlay");
    var anchor = document.querySelector('.dg-anchor[data-diagram="back"]');
    if (anchor) anchor.innerHTML = buildDiagramHTML(overlay ? "back_overlay" : "back");
    ["rowDadoDepth", "rowBackSetback"].forEach(function (id) {
      var row = byId(id); if (row) row.style.display = overlay ? "none" : "";
    });
  }

  /* Arremate: show only the field for the chosen measurement mode, plus a live
     readout of the derived gap (ceiling height - cabinet height) in ceiling mode. */
  function renderArremateMode() {
    var m = byId("arrTopMode"); if (!m) return;
    var ceiling = (m.value === "ceiling");
    var gapRow = byId("rowArrTopGap"), ceilRow = byId("rowArrCeiling"), calc = byId("arrCeilingCalc");
    if (gapRow) gapRow.style.display = ceiling ? "none" : "";
    if (ceilRow) ceilRow.style.display = ceiling ? "" : "none";
    if (calc) {
      if (ceiling) {
        var gap = Math.max(0, num("arrCeilingHeight", 0) - num("H", 0));
        calc.textContent = "Folga calculada (altura do teto − altura do armário): " + gap + " mm";
        calc.style.display = "";
      } else { calc.style.display = "none"; }
    }
  }

  /* Corrediça: the five mounting measurements are a read-only readout of the
     chosen slide while "Personalizar" is off, and editable (authoritative for
     this cabinet) while it is on. */
  var SLIDE_FIELDS = ["side_space", "bottom_clearance", "back_clearance",
                      "box_depth", "min_cabinet_depth"];
  function slideSpecFor(ctx, key) {
    var list = (ctx && ctx.slides) || [];
    for (var i = 0; i < list.length; i++) if (list[i].key === key) return list[i];
    return list[0] || null;
  }
  function syncSlideFields(ctx) {
    var keyEl = byId("slide_key"); if (!keyEl) return;
    var customEl = byId("slide_custom");
    var custom = !!(customEl && customEl.checked);
    var spec = slideSpecFor(ctx, keyEl.value);
    SLIDE_FIELDS.forEach(function (k) {
      var e = byId("slide_" + k); if (!e) return;
      if (!custom && spec && spec[k] != null) e.value = spec[k];
      e.disabled = !custom;
    });
    var info = byId("slideInfo");
    if (info) {
      if (!spec) { info.style.display = "none"; return; }
      info.textContent = (spec.mount === "side"
        ? "Montagem lateral: a corrediça ocupa " + spec.side_space +
          " mm de cada lado, entre a lateral do móvel e a caixa."
        : "Montagem oculta (sob a gaveta): ocupa " + spec.side_space + " mm de cada lado e " +
          spec.bottom_clearance + " mm sob a caixa.")
        + " Profundidade mínima do armário: " + spec.min_cabinet_depth + " mm.";
      info.style.display = "";
    }
  }

  function syncDynamics(ctx) {
    renderBackMode();
    renderArremateMode();
    syncSlideFields(ctx);
    renderJoineryDiagram();
  }

  /* Attach listeners to every rendered control. `ctx.onChange(id)` fires after
     the shared dynamics have run; `ctx.onInput(id)` on every keystroke. */
  function wire(ctx) {
    fieldsFor(ctx.surface, ctx.sections).forEach(function (f) {
      var e = byId(f.id); if (!e) return;
      e.addEventListener("change", function () {
        syncDynamics(ctx);
        if (ctx.onChange) ctx.onChange(f.id);
      });
      e.addEventListener("input", function () {
        // live redraws while typing; the full re-render waits for 'change'
        renderArremateMode();
        renderJoineryDiagram();
        if (ctx.onInput) ctx.onInput(f.id);
      });
    });
  }

  /* ===========================================================================
     FLOATING HELP TOOLTIPS — one element on <body>, delegated events, so tips
     are never clipped by an overflow:hidden card and re-rendered rows need no
     rebinding.
  =========================================================================== */
  function initTooltips() {
    if (initTooltips._done) return;
    initTooltips._done = true;
    var tip = document.createElement("div");
    tip.className = "tip";
    function attach() { document.body.appendChild(tip); }
    if (document.body) attach(); else document.addEventListener("DOMContentLoaded", attach);
    function place(e) {
      var txt = e.getAttribute("data-tip"); if (!txt) return;
      tip.textContent = txt; tip.classList.add("show");
      var r = e.getBoundingClientRect(), tw = tip.offsetWidth, th = tip.offsetHeight;
      var left = Math.max(6, Math.min(r.left + r.width / 2 - tw / 2, window.innerWidth - tw - 6));
      var top = r.top - th - 8; if (top < 6) top = r.bottom + 8;
      tip.style.left = left + "px"; tip.style.top = top + "px";
    }
    function hide() { tip.classList.remove("show"); }
    function tgt(ev) { return ev.target.closest ? ev.target.closest("[data-tip]") : null; }
    document.addEventListener("mouseover", function (ev) { var e = tgt(ev); if (e) place(e); });
    document.addEventListener("mouseout", function (ev) { if (tgt(ev)) hide(); });
    document.addEventListener("focusin", function (ev) { var e = tgt(ev); if (e) place(e); });
    document.addEventListener("focusout", hide);
    document.addEventListener("scroll", hide, true);
  }
  /* Set a tooltip directly on an element the page owns (tiles, buttons, …). */
  function setTip(id, text) {
    var e = byId(id);
    if (e && !e.getAttribute("data-tip")) e.setAttribute("data-tip", text);
  }

  return {
    SPEC: SPEC, DIAGRAMS: DIAGRAMS, FITA_CHOICES: FITA_CHOICES, INHERIT_LABEL: INHERIT_LABEL,
    getPath: getPath, setPath: setPath,
    buildDiagramHTML: buildDiagramHTML,
    render: render, sectionDomId: sectionDomId, sectionDomIds: sectionDomIds,
    fieldsFor: fieldsFor, refreshLists: refreshLists,
    writeForm: writeForm, readForm: readForm, wire: wire,
    syncDynamics: syncDynamics, renderJoineryDiagram: renderJoineryDiagram,
    renderBackMode: renderBackMode, renderArremateMode: renderArremateMode,
    initTooltips: initTooltips, setTip: setTip, makeInfo: makeInfo
  };
})();
