# FusionMob

> 🇧🇷 [Versão em Português mais abaixo](#-fusionmob-português) · 🇺🇸 English version first

An **Autodesk Fusion 360 add-in** for parametric furniture (cabinet) design that also exports a cut list ready for import into **CorteCloud** (a Brazilian panel-cutting / nesting service). It is built for Brazilian *marcenaria* (custom cabinetmaking) workflows.

---

## 🇺🇸 FusionMob (English)

### What is it?

FusionMob adds a **FusionMob** ribbon tab (in Fusion 360's Solid environment) with a **Cabinet** panel. From there you design parametric panels and full cabinet carcasses in 3D, and then export a cut list as a CSV that CorteCloud can import directly.

It works in two layers:

- **Geometry layer** — builds real 3D solid bodies (one component per panel) and stores each panel's cut-list definition as JSON inside a Fusion *body attribute*. Cabinets also store their full creation config, so they can be edited and rebuilt non-destructively.
- **Export layer** — reads those stored attributes back out and writes the CorteCloud-compatible CSV.

### Commands

| Command | Purpose |
|---|---|
| **New Panel** | Create a single parametric panel (board) with dimensions, material, function, and edge-banding (*fita*) per edge. |
| **New Cabinet** | Generate a full frameless cabinet carcass: two sides, base, top, N evenly-spaced shelves, an optional grooved back panel, an optional toe kick (*rodapé*), optional doors (*portas*) and drawers (*gavetas*) with embedded slide hardware. |
| **Edit Cabinet** | Pick an existing cabinet, tweak any parameter, and regenerate it in place. |
| **Cabinet Layout** | Open a visual HTML palette to divide the cabinet interior into a recursive grid of *regions* — each independently open / shelves / doors / drawers — split, resize or merge them, then **Apply** to (re)generate the whole cabinet. |
| **Export Cut List** | Collect every tagged panel in the document and write a CorteCloud-compatible CSV. |

### Requirements

- **Autodesk Fusion 360** (Windows or Mac).
- A design in **Assembly** or **Hybrid** mode (not a Part design — those allow only one component; the add-in will show a friendly error otherwise).

### Installation

1. **Download / clone** this repository to a local folder.
2. In Fusion 360, open the **Utilities** tab → **ADD-INS** → **Scripts and Add-Ins** (or press `Shift+S`).
3. Go to the **Add-Ins** tab, click the green **+** next to *My Add-Ins*, and select the `FusionMob` folder (the one containing `FusionMob.py` and `FusionMob.manifest`).

   > Alternatively, copy the `FusionMob` folder into Fusion's add-ins directory so it is picked up automatically:
   > - **Windows:** `%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\`
   > - **Mac:** `~/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns/`

4. Select **FusionMob** in the list and click **Run**. Tick **Run on Startup** to load it automatically every session.
5. Switch to the **Solid** environment — a new **FusionMob** tab with a **Cabinet** panel appears in the ribbon.

To update, replace the folder contents and restart the add-in (or Fusion).

### How it works

- **Units:** the UI and CSV use **millimeters**; Fusion's internal geometry uses centimeters (the code converts automatically).
- **Finished sizes:** all dimensions are *finished* sizes — CorteCloud deducts edge-tape thickness itself, so nothing is pre-deducted. Panel thickness is encoded in the material name (e.g. `MDF 18mm Branco`).
- **Interior layout (region grid):** the cabinet interior is a recursive tree of *regions*. Each region can be open, shelves, doors or drawers, and can be split horizontally or vertically into more regions. Because the type is per-region, **shelves, doors and drawers can coexist in one cabinet**. The **Cabinet Layout** palette is the visual editor for this tree; a classic New/Edit cabinet reproduces the full single-region carcass exactly.
- **Back panel** seats in dado grooves cut into the sides/base/top, with configurable clearances.
- **Toe kick** (*rodapé*) is a separate box the carcass rests on (standard Brazilian practice); the cabinet's overall height includes it.
- **Doors** are frameless, edge-banded on all four edges and grain-locked. They can be **overlay** (*sobreposta*) or **inset** (*embutida*), and each door gets its own hinge (revolute) joint so it swings open like a real door. Concealed-hinge cup bores and mounting-plate pilots are cut as model-only geometry (they don't reach the CSV; the hinge count is noted in the part's *Complemento* instead).
- **Drawers** are BR-standard boxes (2 sides + front + back + a grooved bottom) plus a separate face, sized from the real drawer-slide planning spec. Each drawer gets a slider (prismatic) joint so it opens toward the front.
- **Drawer slide hardware** is chosen from a bundled library manifest (`resources/hardware/hardware.json`). By default a lightweight parametric proxy marks each slide; ticking *Insert 3D slide model* imports the real bundled CAD instead. Hardware never reaches the CSV — the chosen slide is noted in the drawer face's *Complemento*.

### Exporting the cut list

Run **Export Cut List** to collect every tagged panel in the document and write a **semicolon-delimited, UTF-8-with-BOM** CSV that CorteCloud can import via *"importar do Excel"*. Columns:

```
Quantidade;Comprimento;Largura;Funcao;Fita C1;Fita C2;Fita L1;Fita L2;Material;Complemento;Girar
```

`Comprimento` is the larger face dimension, `Largura` the smaller, and `Girar` (`Sim`/`Nao`) tells CorteCloud whether it may rotate the part during nesting (grain lock). See [`cortecloud_importar.csv`](cortecloud_importar.csv) for a sample export.

### Project layout

```
FusionMob/
├── FusionMob.py         # the add-in (lifecycle, commands, geometry, region grid, palette, export)
├── FusionMob.manifest   # Fusion 360 add-in manifest
├── resources/
│   ├── hardware/        # drawer-slide library: hardware.json manifest + models/ (bundled CAD)
│   └── ui/              # layout_editor.html — the Cabinet Layout palette
└── .vscode/launch.json  # Fusion 360 Python debugging config
cortecloud_importar.csv  # sample exported cut list
```

---
---

## 🇧🇷 FusionMob (Português)

Um **add-in do Autodesk Fusion 360** para projeto paramétrico de móveis (armários) que também exporta uma lista de corte pronta para importar no **CorteCloud** (serviço brasileiro de corte / nesting de chapas). Foi feito para o fluxo de trabalho da **marcenaria** brasileira.

### O que é?

O FusionMob adiciona uma aba **FusionMob** na faixa de opções (no ambiente Solid do Fusion 360), com um painel **Cabinet**. A partir dele você projeta painéis paramétricos e carcaças completas de armários em 3D e depois exporta a lista de corte em CSV que o CorteCloud importa diretamente.

Ele funciona em duas camadas:

- **Camada de geometria** — cria corpos sólidos 3D reais (um componente por painel) e guarda a definição de corte de cada painel como JSON dentro de um *atributo de corpo* do Fusion. Os armários também guardam toda a sua configuração de criação, permitindo editar e reconstruir de forma não destrutiva.
- **Camada de exportação** — lê esses atributos de volta e grava o CSV compatível com o CorteCloud.

### Comandos

| Comando | Função |
|---|---|
| **New Panel** | Cria um único painel paramétrico (chapa) com dimensões, material, função e fita de borda por aresta. |
| **New Cabinet** | Gera uma carcaça completa sem quadro: duas laterais, base, tampo, N prateleiras igualmente espaçadas, fundo opcional (encaixado em rasgo), rodapé opcional, portas e gavetas opcionais com corrediças embutidas. |
| **Edit Cabinet** | Seleciona um armário existente, ajusta qualquer parâmetro e o regenera no lugar. |
| **Cabinet Layout** | Abre uma paleta visual (HTML) para dividir o interior do armário em uma grade recursiva de *regiões* — cada uma independente: aberta / prateleiras / portas / gavetas — dividir, redimensionar ou unir, e depois **Apply** para (re)gerar o armário inteiro. |
| **Export Cut List** | Reúne todos os painéis marcados no documento e grava um CSV compatível com o CorteCloud. |

### Requisitos

- **Autodesk Fusion 360** (Windows ou Mac).
- Um design em modo **Assembly** ou **Hybrid** (não use Part design — ele permite apenas um componente; o add-in exibirá um erro amigável nesse caso).

### Instalação

1. **Baixe / clone** este repositório em uma pasta local.
2. No Fusion 360, abra a aba **Utilities** → **ADD-INS** → **Scripts and Add-Ins** (ou pressione `Shift+S`).
3. Vá até a aba **Add-Ins**, clique no **+** verde ao lado de *My Add-Ins* e selecione a pasta `FusionMob` (a que contém `FusionMob.py` e `FusionMob.manifest`).

   > Como alternativa, copie a pasta `FusionMob` para o diretório de add-ins do Fusion, para que seja carregada automaticamente:
   > - **Windows:** `%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\`
   > - **Mac:** `~/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns/`

4. Selecione **FusionMob** na lista e clique em **Run**. Marque **Run on Startup** para carregá-lo automaticamente a cada sessão.
5. Vá para o ambiente **Solid** — uma nova aba **FusionMob** com o painel **Cabinet** aparece na faixa de opções.

Para atualizar, substitua o conteúdo da pasta e reinicie o add-in (ou o Fusion).

### Como funciona

- **Unidades:** a interface e o CSV usam **milímetros**; a geometria interna do Fusion usa centímetros (a conversão é automática).
- **Medidas acabadas:** todas as dimensões são medidas *acabadas* — o próprio CorteCloud desconta a espessura da fita, então nada é pré-descontado. A espessura da chapa vai no nome do material (ex.: `MDF 18mm Branco`).
- **Layout interno (grade de regiões):** o interior do armário é uma árvore recursiva de *regiões*. Cada região pode ser aberta, prateleiras, portas ou gavetas, e pode ser dividida na horizontal ou na vertical em mais regiões. Como o tipo é por região, **prateleiras, portas e gavetas podem coexistir em um mesmo armário**. A paleta **Cabinet Layout** é o editor visual dessa árvore; um armário clássico (New/Edit) reproduz exatamente a carcaça de região única.
- **Fundo** encaixa em rasgos (dado) cortados nas laterais/base/tampo, com folgas configuráveis.
- **Rodapé** é uma caixa separada sobre a qual a carcaça se apoia (prática padrão no Brasil); a altura total do armário já o inclui.
- **Portas** são sem quadro, com fita nas quatro bordas e veio travado. Podem ser **sobrepostas** ou **embutidas (inset)**, e cada porta ganha sua própria junta de dobradiça (revolute) para abrir como uma porta de verdade. Os furos de caneco da dobradiça oculta e os pilotos da placa são cortados como geometria apenas de modelo (não vão para o CSV; a quantidade de dobradiças é anotada no *Complemento* da peça).
- **Gavetas** são caixas no padrão BR (2 laterais + frente + fundo da caixa + um fundo encaixado em rasgo) mais uma frente separada, dimensionadas a partir da especificação real de projeto da corrediça. Cada gaveta ganha uma junta deslizante (prismatic) para abrir para a frente.
- **Corrediças** são escolhidas de uma biblioteca embutida (`resources/hardware/hardware.json`). Por padrão, um proxy paramétrico leve marca cada corrediça; ao marcar *Inserir modelo 3D da corrediça*, o CAD real embutido é importado. As ferragens nunca vão para o CSV — a corrediça escolhida é anotada no *Complemento* da frente da gaveta.

### Exportando a lista de corte

Execute **Export Cut List** para reunir todos os painéis marcados no documento e gravar um CSV **separado por ponto-e-vírgula, UTF-8 com BOM** que o CorteCloud importa via *"importar do Excel"*. Colunas:

```
Quantidade;Comprimento;Largura;Funcao;Fita C1;Fita C2;Fita L1;Fita L2;Material;Complemento;Girar
```

`Comprimento` é a maior dimensão da face, `Largura` a menor, e `Girar` (`Sim`/`Nao`) diz ao CorteCloud se ele pode girar a peça durante o nesting (trava de veio). Veja [`cortecloud_importar.csv`](cortecloud_importar.csv) para um exemplo de exportação.

### Estrutura do projeto

```
FusionMob/
├── FusionMob.py         # o add-in (ciclo de vida, comandos, geometria, grade de regiões, paleta, exportação)
├── FusionMob.manifest   # manifesto do add-in do Fusion 360
├── resources/
│   ├── hardware/        # biblioteca de corrediças: manifesto hardware.json + models/ (CAD embutido)
│   └── ui/              # layout_editor.html — a paleta Cabinet Layout
└── .vscode/launch.json  # configuração de debug Python do Fusion 360
cortecloud_importar.csv  # exemplo de lista de corte exportada
```

---

**Versão / Version:** 1.3.1
