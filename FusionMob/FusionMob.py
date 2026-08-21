import adsk.core
import adsk.fusion
import traceback
import json
import csv
import os
import math

# -----------------------------------------------------------------------------
# FusionMob - Phase 1: parametric panel editor + CorteCloud cut list exporter
#
# Architecture (from the design chat):
#   Layer 1 (this add-in) generates the 3D geometry and stores each panel's
#           definition as JSON in a body attribute inside the Fusion document.
#   Layer 2 (the exporter) reads those definitions and writes a CorteCloud CSV.
#
# The CSV matches CorteCloud's "importar do Excel" template, columns:
#   Quantidade | Comprimento | Largura | Funcao | Fita C1 | Fita C2 |
#   Fita L1 | Fita L2 | Material | Complemento | Girar
#
# Dimensions exported are FINISHED sizes: CorteCloud deducts the edge-tape
# (fita) thickness itself based on the Fita columns, so we do NOT pre-deduct.
# Thickness is encoded in the Material name (e.g. "MDF 18mm Branco").
# -----------------------------------------------------------------------------

# Add-in version (keep in sync with FusionMob.manifest). Bump the patch digit
# (last number) on every modification — see CLAUDE.md "Versioning".
__version__ = '1.20.0'

app = None
ui = None
handlers = []

# The right-clicked cabinet's entity token, set by the marking-menu handler so
# the Edit Cabinet dialog can pre-select it. Cleared once consumed.
_context_edit_token = None
# The right-clicked tagged panel body's entity token, so the Edit Panel dialog
# can pre-select it. Cleared once consumed.
_context_panel_token = None
# Reference to the marking-menu handler so stop() can unregister it.
_marking_menu_handler = None

WORKSPACE_ID = 'FusionSolidEnvironment'
TAB_ID = 'FusionMobTab'
TAB_NAME = 'FusionMob'
PANEL_ID = 'FusionMobPanel'

NEW_PANEL_CMD_ID = 'FusionMobNewPanel'
EDIT_PANEL_CMD_ID = 'FusionMobEditPanel'
NEW_CABINET_CMD_ID = 'FusionMobNewCabinet'
EDIT_CABINET_CMD_ID = 'FusionMobEditCabinet'
LAYOUT_CMD_ID = 'FusionMobCabinetLayout'
EXPORT_CMD_ID = 'FusionMobExportCutList'
PREFS_CMD_ID = 'FusionMobPreferences'

# Interior-layout editor palette (HTML). Reference kept so stop() can unregister.
LAYOUT_PALETTE_ID = 'FusionMobLayoutPalette'
_layout_palette_handler = None
# Preferences editor palette (HTML). Reference kept so stop() can unregister.
PREFS_PALETTE_ID = 'FusionMobPrefsPalette'
_prefs_palette_handler = None

# Attribute group/name used to tag panels we generate.
ATTR_GROUP = 'FusionMob'
ATTR_NAME = 'panelData'
# Attribute (on the cabinet component) holding its full creation config as JSON.
CABINET_CFG_ATTR = 'cabinetConfig'

# Material library. Display name (as registered in CorteCloud) + thickness mm.
# Thickness drives the 3D extrude; CorteCloud reads thickness from the name.
MATERIALS = [
    ('MDF 18mm Branco', 18.0),
    ('MDF 15mm Branco', 15.0),
    ('MDF 16mm Branco', 16.0),
    ('MDF 6mm Cru', 6.0),
    ('MDP 18mm Branco', 18.0),
]

# Common part roles for the CorteCloud "Funcao" column.
FUNCOES = ['Lateral', 'Tampo', 'Base', 'Prateleira', 'Fundo', 'Rodape', 'Porta', 'Travessa', 'Outro']

# "Girar": may CorteCloud rotate the part during nesting? "Nao" locks the grain.
GIRAR_OPTIONS = ['Sim', 'Nao']

# European concealed-hinge (dobradiça de caneco) boring standard, model-only.
# A cup is bored blind into the door's back face; a mounting plate is fixed to
# the adjacent side panel's interior face by two screws. These are the standard
# 35mm-cup dimensions used across BR/European hardware (Blum/FGV/etc.).
HINGE = {
    'cup_diameter': 35.0,    # cup (caneco) bore diameter (mm)
    'cup_depth': 12.0,       # blind bore depth into the door back face (mm)
    'cup_edge': 22.5,        # door edge (hinge side) to cup CENTRE (mm)
    'end_inset': 100.0,      # first/last hinge centre from door top/bottom (mm)
    'screw_diameter': 5.0,   # mounting-plate screw pilot hole (mm)
    'screw_depth': 12.0,     # screw pilot depth into the side panel (mm)
    'plate_front': 37.0,     # front screw centre from the front edge, y=0 (mm)
    'screw_pitch': 32.0,     # plate screw spacing along depth, System 32 (mm)
    'shelf_clearance': 30.0, # min gap from a shelf face to a hinge centre (mm)
}

# Drawer (gaveta) construction defaults (mm). A BR-standard drawer box: 2 sides +
# front + back cut from the box material, plus a bottom seated in a dado groove in
# all four walls, and a separate face (frente) mounted on the front (overlay or
# inset like a door, banded all round, grain-locked). Box/bottom sub-specs stay at
# these defaults; only materials + the face band are exposed in the dialog for now.
DRAWER = {
    'box_material': 'MDF 16mm Branco',   # sides/front/back of the drawer box
    'box_t': 16.0,                       # box wall thickness (mm) — Matrix UM A30 is designed for 16mm sides
    'bottom_material': 'MDF 6mm Cru',    # drawer bottom (seated in a dado)
    'bottom_t': 6.0,
    'bottom_dado_depth': 6.0,            # groove depth into each wall for the bottom
    'bottom_up': 12.0,                   # bottom groove Z above the box lower edge
    'bottom_play': 0.4,                  # groove height play around the bottom panel
    'box_height': 150.0,                 # max drawer box SIDE height (mm); the tall
                                         # face covers the opening, the box behind is
                                         # shorter.
    'box_top_gap': 30.0,                 # clearance (mm) from the box top to the
                                         # drawer above (box top stops this far below
                                         # its face top / the interior ceiling).
    'face_material': 'MDF 18mm Branco',  # front face (frente)
    'face_t': 18.0,
    'face_band': 'Fita PVC 1mm Branco',  # face banded all four edges
    'back_height_reduction': 0.0,        # box back shorter than sides (0 = equal)
}

# Per-cabinet override of the chosen slide's mounting numbers, so a shop can dial
# in the runner it actually buys without touching hardware.json (which lives in the
# read-only install folder). Ignored entirely while 'custom' is False — the library
# spec then wins — so the values below are just the seed the dialog shows. Every
# field maps onto the resolved spec in resolve_slide_spec. See FALLBACK_SLIDE for
# what each number means.
SLIDE = {
    'custom': False,
    'side_space': 12.7,          # -> spec['side_space']         (espaço por lado)
    'bottom_clearance': 3.0,     # -> spec['bottom_clearance']   (folga sob a caixa)
    'back_clearance': 10.0,      # -> spec['back_clearance']     (folga no fundo)
    'box_depth': 350.0,          # -> spec['recommended_box_depth']
    'min_cabinet_depth': 380.0,  # -> spec['min_cabinet_depth']
}

# The spec fields cfg['slide'] overrides, as (override key, spec key).
_SLIDE_OVERRIDE_FIELDS = (
    ('side_space', 'side_space'),
    ('bottom_clearance', 'bottom_clearance'),
    ('back_clearance', 'back_clearance'),
    ('box_depth', 'recommended_box_depth'),
    ('min_cabinet_depth', 'min_cabinet_depth'),
)


def hinge_count_for_height(h_mm):
    """Number of hinges per door by height, following common BR shop practice:
    2 up to 900mm, 3 up to 1600mm, 4 up to 2000mm, 5 beyond."""
    if h_mm <= 900.0:
        return 2
    if h_mm <= 1600.0:
        return 3
    if h_mm <= 2000.0:
        return 4
    return 5


def hinge_z_positions(door_h_mm, door_z0_c, end_inset_mm=None):
    """Z centres (cm, cabinet coords) of a door's hinges. door_z0_c is the door's
    bottom. First/last are inset from the ends by `end_inset_mm` (defaults to the
    HINGE standard); the rest are evenly spaced."""
    n = hinge_count_for_height(door_h_mm)
    door_h_c = door_h_mm / 10.0
    if n <= 1:
        return [door_z0_c + door_h_c / 2.0]
    ei = HINGE['end_inset'] if end_inset_mm is None else end_inset_mm
    inset_c = min(ei, door_h_mm / 2.0) / 10.0
    bot = door_z0_c + inset_c
    top = door_z0_c + door_h_c - inset_c
    step = (top - bot) / (n - 1)
    return [bot + i * step for i in range(n)]


def shelf_z_bottoms(Hbox, t, n_shelves, z_off_c):
    """Z (cm, cabinet coords) of each shelf's bottom face, evenly distributed
    between base and top. Returns [] when the config leaves no room — the shelf
    builder validates and raises the friendly error separately."""
    if n_shelves <= 0:
        return []
    opening = Hbox - 2 * t
    gap = (opening - n_shelves * t) / (n_shelves + 1)
    if gap <= 0:
        return []
    gap_c, tc = gap / 10.0, t / 10.0
    base_top_c = z_off_c + tc
    return [base_top_c + i * gap_c + (i - 1) * tc for i in range(1, n_shelves + 1)]


def resolve_hinge_conflicts(hinge_zs, shelf_bottoms, tc, lo, hi, clearance_c):
    """Nudge each hinge Z (cm) clear of any shelf. A shelf spans [z0, z0+tc] on
    the side panel's interior face — the same face the hinge plate mounts to — so
    a hinge landing within `clearance_c` of that band would clash with the shelf.
    Each conflicting hinge is moved to the nearest band edge that stays within the
    door's usable range [lo, hi] and clears every shelf. Returns (resolved_zs,
    unresolved_zs); unresolved hinges are left at their original Z for a warning."""
    bands = [(z0 - clearance_c, z0 + tc + clearance_c) for z0 in shelf_bottoms]

    def hit(z):
        for a, b in bands:
            if a < z < b:
                return (a, b)
        return None

    resolved, unresolved = [], []
    for z in hinge_zs:
        band = hit(z)
        if not band:
            resolved.append(z)
            continue
        a, b = band
        # Candidate spots just clear of this band, nearest to the original first.
        options = [c for c in (a, b) if lo <= c <= hi and hit(c) is None]
        if options:
            resolved.append(min(options, key=lambda c: abs(c - z)))
        else:
            unresolved.append(z)
            resolved.append(z)
    return resolved, unresolved


# Default fit tolerances (mm). Tunable in the cabinet "Advanced" section.
DEFAULT_TOL = {
    'dado_bottom_clearance': 0.5,   # back stops this far from the groove bottom
    'dado_side_clearance': 0.2,     # play between back faces and groove walls (per side)
    'shelf_back_gap': 1.0,          # gap between shelf rear edge and the back panel
    'shelf_front_setback': 30.0,    # how far shallower shelves sit back from the front
    'shelf_door_clearance': 2.0,    # gap from a closed door's inner face to the shelf front
}

# Edge banding (fita) defaults. CorteCloud's CSV has four Fita columns (C1/C2 on
# the Comprimento edges, L1/L2 on the Largura edges); a panel is banded only on
# its VISIBLE edges, and the tape comes in two thicknesses. 'name_thin' (0.4mm)
# and 'name_thick' (1mm) are free-text tape names (colour follows the operator);
# 'carcass' picks the thickness for the front edge of sides/base/top/shelves/
# dividers, 'fronts' the thickness for doors/drawer faces (all four edges) and
# the toe-kick front board. Each is 'none' | 'thin' | 'thick'. Deep-merged in
# normalize_cfg like HINGE/DRAWER/DEFAULT_TOL, so switching a group's thickness
# (or turning it off) re-bands the whole cut list on the next build.
FITA = {
    'name_thin':  'Fita PVC 0.4mm Branco',   # the 0.4mm tape
    'name_thick': 'Fita PVC 1mm Branco',     # the 1mm tape
    'carcass': 'thin',    # sides/base/top/shelves/dividers front edge
    'fronts':  'thick',   # doors + drawer faces (all 4) + toe-kick front
}

# Side<->base/top joinery (Fixacao Lateral, Promob's "Configurar Dimensoes").
# Chooses, per junction (bottom base + top/tampo), how the side panels meet the
# horizontal panel:
#   'aligned' -> base/top captured BETWEEN the sides; the sides run full box
#                height (today's behaviour, "Lateral alinhada com base").
#   'over'    -> base/top run the FULL width and the side sits on/under them
#                ("Lateral sobre base"). The overhang (mm) sets how far the side
#                extends past the panel's inner face toward the panel's outer
#                face: 0 = side rests on the base top / under the top bottom
#                (Promob "auto"); == t = side flush with the base bottom / top
#                top; > t = side skirts beyond the panel (Promob "fixo", e.g.
#                50mm), clamped to the cabinet envelope (floor / overall height).
# Deep-merged in normalize_cfg like HINGE/DRAWER/FITA. Both 'aligned' reproduces
# the classic geometry exactly. The interior datums (base top face / top bottom
# face) never move, so the region grid, back panel, shelves, doors, drawers and
# hinges are unaffected; only the side Z-extent and the base/top width change.
# v1 scope: vertical joinery only -- the depth-direction Folga/Alinhamento
# (Promob F/G/H/I) is not modelled.
JOINERY = {
    'bottom_mode': 'aligned',   # 'aligned' | 'over'
    'bottom_overhang': 0.0,     # mm, used only when bottom_mode == 'over'
    'top_mode': 'aligned',      # 'aligned' | 'over'
    'top_overhang': 0.0,        # mm, used only when top_mode == 'over'
    # Toe-kick integration: when True (and there IS a toe kick), the side panels
    # run all the way DOWN to the floor (Z=0), forming the cabinet legs (pes
    # laterais), and the toe-kick front board + back rail + reinforcements span
    # BETWEEN the sides (recessed at the front) with no separate end legs. When
    # False (default) the sides stop at the base bottom (z_off) and the toe kick
    # is a separate full-width box below them (today's behaviour). The base top
    # face stays at z_off+tc either way, so the interior is unaffected.
    'sides_to_floor': False,
}


# Tamponamento (acabamento): applied finishing panels over exposed carcass faces.
# Each enabled face adds an applied panel OUTSIDE the structural face, so the
# overall envelope grows by 't' per side / top panel. Sides run full height
# (floor-to-top, covering the toe-kick recess) x full depth; the top caps the
# full finished width (incl. side tamponamentos when present). The interior
# datums never move -> region grid, back, shelves, doors, drawers and hinges are
# all unaffected (modelled like the overlay back panel). Cut-list only, no
# usinagem. v1 limitation: the panels extend past the nominal W x H envelope, but
# the published fmob_* user parameters and multi-cabinet spacing still use the
# nominal W x H x D (fine for a single exposed-end cabinet).
TAMPONAMENTO = {
    'left': False,          # apply over Lateral Esquerda
    'right': False,         # apply over Lateral Direita
    'top': False,           # apply over Tampo
    't': 18.0,              # thickness (mm)
    'material': '',         # '' -> inherit the carcass material
    # How far the finish panels project FORWARD past the carcass front face (mm).
    # 0 = flush with the front. The panels' rear edge always stays flush with the
    # cabinet back (y = D), so a positive value makes them DEEPER than the carcass
    # (depth = D + front_overhang) and they overhang the front. Applied uniformly
    # to the side and top panels so the finished shell lines up at the front.
    'front_overhang': 0.0,
}


# Arremate (ajuste): scribe / gap-filler pieces that let a cabinet "reach" an
# uncertain ceiling and/or side walls. Standard BR practice: build the carcass a
# little short, then close the gaps with front trim pieces that are cut oversized
# and scribed / trimmed to fit on site. Two kinds, both modelled as thin FRONT
# boards (thickness in Y, front face flush with the carcass front y=0), grain
# locked and banded like a visible front:
#   * TOP (sanefa frontal): a valance board spanning the carcass width, standing
#     from the carcass top (Hc) up by 'top_gap' to meet the ceiling. Open behind
#     -> it hides the gap as seen from the front, giving the "full height" look.
#     'top_side_returns' (on by default) wraps it into a U with a full-depth return
#     over each carcass side, so exposed ends look finished (the sides read as
#     continuing to the ceiling); the front board then spans only between them.
#   * SIDES (regua frontal): a front filler strip beside a carcass side, 'side_gap'
#     wide (X), running the full finished height (floor to ceiling when a top
#     sanefa is present, else floor to carcass top), scribed to the wall.
# Like TAMPONAMENTO these sit OUTSIDE the carcass, so the envelope grows (up by
# top_gap, out by side_gap per side) and the interior datums never move -> region
# grid, back, shelves, doors, drawers and hinges are all unaffected. Cut-list
# only (no usinagem); each piece carries an "ajustar no local" note so the shop
# knows to cut it oversized and trim on install. v1: side and top gaps are closed
# by independent pieces (sides run full height, the sanefa spans between them),
# so the pieces never overlap; combining arremate with tamponamento on the SAME
# side is out of scope (they would share volume).
#
# 'top_inline_fronts' faces the sanefa with the doors/drawers instead of leaving
# it recessed at the carcass front. Overlay fronts project forward by their own
# thickness (door front at y=-door_t, drawer face at y=-face_t), so a sanefa flush
# at y=0 sits BEHIND the front plane. With this flag on, the structural sanefa
# stays flush at the carcass front (screwed to the carcass) and a SECOND facing
# sheet is added in front of it, its depth filling the gap so its visible face
# lands exactly on the overlay front plane. The fill depth is derived from the
# fronts (max overlay reach of enabled non-inset doors/drawers); with only inset
# fronts (already at y=0) or no fronts the flag is a no-op.
ARREMATE = {
    'top': False,           # sanefa frontal to reach the ceiling
    # How the sanefa height (carcass-top -> ceiling gap) is specified:
    #   'gap'     -> use 'top_gap' directly (the measured gap).
    #   'ceiling' -> derive the gap from the total floor->ceiling height
    #                ('ceiling_height' - cabinet height H). Lets the user enter the
    #                room's ceiling height and have the gap computed automatically.
    # See resolve_arremate_top_gap (the single seam every consumer reads through).
    'top_gap_mode': 'gap',
    'top_gap': 50.0,        # gap carcass-top -> ceiling (mm) = sanefa height (mode 'gap')
    'ceiling_height': 2400.0,  # total floor->ceiling height (mm), used in mode 'ceiling'
    'top_inline_fronts': False,  # face the sanefa with the doors/drawers (adds a front sheet)
    'top_side_returns': True,    # U-shape: wrap the sanefa with side returns (looks good on exposed ends)
    'left': False,          # left regua frontal (gap to the left wall)
    'right': False,         # right regua frontal (gap to the right wall)
    'side_gap': 30.0,       # gap carcass-side -> wall (mm) = each strip width
    't': 18.0,              # thickness of the filler pieces (mm)
    'material': '',         # '' -> inherit the carcass material
}


# Top-sanefa gap input modes (Arremate). 'gap' = enter the measured carcass-top
# -> ceiling gap directly; 'ceiling' = enter the total floor->ceiling height and
# derive the gap (ceiling_height - H). See resolve_arremate_top_gap.
ARREMATE_TOP_MODE_CHOICES = [('gap', 'Informar folga'),
                             ('ceiling', 'Informar altura do teto')]


def _arremate_top_mode_label(value):
    for v, lbl in ARREMATE_TOP_MODE_CHOICES:
        if v == value:
            return lbl
    return ARREMATE_TOP_MODE_CHOICES[0][1]


def _arremate_top_mode_value(label):
    for v, lbl in ARREMATE_TOP_MODE_CHOICES:
        if lbl == label:
            return v
    return 'gap'


def resolve_arremate_top_gap(cfg):
    """Effective top-sanefa gap in mm (carcass-top -> ceiling).

    In 'ceiling' mode the gap is derived from the total floor->ceiling height
    minus the cabinet height H (never negative); otherwise the stored 'top_gap'
    is used verbatim. Pure — no adsk dependency, unit-testable. This is the single
    seam the builder and validation read the gap through, so the two can't drift.
    """
    arr = cfg.get('arremate') or ARREMATE
    if arr.get('top_gap_mode') == 'ceiling':
        return max(0.0, float(arr.get('ceiling_height', 0.0)) - float(cfg.get('H', 0.0)))
    return float(arr.get('top_gap', 0.0))


# Puxador integrado (frente estendida): the handleless cabinet. Instead of a
# handle, the FRONT is made longer than the region it covers and overhangs the
# carcass at the bottom (or the top), so the protruding edge becomes the lip the
# user hooks their fingers on to pull the door / drawer open.
#
# Only OVERLAY (sobreposta) fronts can do this — an inset front sits inside the
# opening and has nowhere to extend to — and only the fronts that reach the
# cabinet's own bottom/top edge (a front in the middle of a stacked layout would
# run into its neighbour). Everything else is untouched: the lip only grows the
# front panel, so the carcass, interior datums, region grid, back, shelves,
# drawer boxes and hinge boring are all unaffected. Cut-list only (the taller
# front is simply a bigger part) — the lip is noted in Complemento.
#
# 'size' is how far the front reaches past the carcass edge (mm). With a bottom
# lip the front hangs in FRONT of the toe-kick recess (the kick is set back, so
# nothing shares volume) — that is exactly what makes the grip work, and it is
# why the lip is clamped to the kick height: any longer and the front would run
# past the floor. See resolve_grip_size, the single seam every consumer reads.
PUXADOR = {
    'enabled': False,      # off by default: cabinets keep their plain fronts
    'side': 'bottom',      # 'bottom' (aba inferior) | 'top' (aba superior)
    'size': 40.0,          # how far the front extends past the carcass edge (mm)
}


# Which edge of the cabinet the handleless grip lip extends past. See PUXADOR.
PUXADOR_SIDE_CHOICES = [('bottom', 'Embaixo (aba inferior)'),
                        ('top', 'Em cima (aba superior)')]


def _puxador_side_label(value):
    for v, lbl in PUXADOR_SIDE_CHOICES:
        if v == value:
            return lbl
    return PUXADOR_SIDE_CHOICES[0][1]


def _puxador_side_value(label):
    for v, lbl in PUXADOR_SIDE_CHOICES:
        if lbl == label:
            return v
    return 'bottom'


def resolve_grip_size(cfg):
    """Effective handleless grip-lip length in mm (0 when the option is off).

    A bottom lip hangs in front of the toe-kick recess, so it is clamped to the
    kick height — a longer one would reach past the floor. Pure — no adsk
    dependency, unit-testable. This is the single seam the builder and validation
    read the lip through, so the two can't drift."""
    px = cfg.get('puxador') or PUXADOR
    if not px.get('enabled'):
        return 0.0
    size = max(0.0, float(px.get('size', 0.0) or 0.0))
    if px.get('side', 'bottom') != 'top' and cfg.get('with_toe_kick'):
        size = min(size, max(0.0, float(cfg.get('toe_kick_height', 0.0) or 0.0)))
    return size


def grip_lip_extents(px, size_mm, band, carcass_z0_c, carcass_z1_c, inset):
    """(bottom, top) extra front reach in cm for one leaf band.

    Non-zero only for an OVERLAY front whose overlay reach already lands on the
    cabinet's own bottom/top edge — the only place a lip has room to hang. Pure:
    `carcass_z0_c`/`carcass_z1_c` are the carcass bottom/top faces (cm)."""
    if inset or size_mm <= 0:
        return 0.0, 0.0
    size_c = size_mm / 10.0
    eps = 1e-6
    if px.get('side', 'bottom') == 'top':
        return (0.0, size_c) if abs((band.z1 + band.ext_t) - carcass_z1_c) <= eps else (0.0, 0.0)
    return (size_c, 0.0) if abs((band.z0 - band.ext_b) - carcass_z0_c) <= eps else (0.0, 0.0)


def grip_note(lip_b_c, lip_t_c):
    """Complemento note for a front carrying a handleless grip lip (cm extents),
    or '' when it has none. CorteCloud has no usinagem field, so the lip is
    called out in the part's Complemento (same convention as the hinge/slide
    furacao notes) — the part itself is simply cut longer."""
    if lip_b_c > 0:
        return 'puxador integrado: aba de {0:.0f}mm embaixo'.format(lip_b_c * 10.0)
    if lip_t_c > 0:
        return 'puxador integrado: aba de {0:.0f}mm em cima'.format(lip_t_c * 10.0)
    return ''


def fita_tape(fita_cfg, group):
    """Resolve a fita group ('carcass'/'fronts') to its tape name, or '' when the
    group is off ('none'). Pure — no adsk dependency, unit-testable."""
    choice = (fita_cfg or {}).get(group, 'none')
    if choice == 'thin':
        return fita_cfg.get('name_thin', FITA['name_thin'])
    if choice == 'thick':
        return fita_cfg.get('name_thick', FITA['name_thick'])
    return ''


# Dropdown labels for a fita thickness choice, in order. Value <-> label helpers.
FITA_CHOICES = [('none', 'Nenhuma'), ('thin', '0.4mm (fina)'), ('thick', '1mm (grossa)')]


def _fita_choice_label(value):
    for v, lbl in FITA_CHOICES:
        if v == value:
            return lbl
    return FITA_CHOICES[0][1]


def _fita_choice_value(label):
    for v, lbl in FITA_CHOICES:
        if lbl == label:
            return v
    return 'none'


# Side<->base/top joinery modes (Fixacao Lateral). See JOINERY.
JOINERY_CHOICES = [('aligned', 'Alinhada com base'), ('over', 'Sobre base')]


def _joinery_choice_label(value):
    for v, lbl in JOINERY_CHOICES:
        if v == value:
            return lbl
    return JOINERY_CHOICES[0][1]


def _joinery_choice_value(label):
    for v, lbl in JOINERY_CHOICES:
        if lbl == label:
            return v
    return 'aligned'


# Back-panel mounting modes (Fixacao do fundo). 'groove' = seated in dado grooves
# cut into the sides/base/top (the classic encaixado fundo); 'overlay' = a
# full-width panel simply applied/screwed to the rear of the carcass (sobreposto),
# no grooves. See build_cabinet's back-panel block.
BACK_MODE_CHOICES = [('groove', 'Encaixado (ranhura)'), ('overlay', 'Sobreposto (atras)')]


def _back_mode_choice_label(value):
    for v, lbl in BACK_MODE_CHOICES:
        if v == value:
            return lbl
    return BACK_MODE_CHOICES[0][1]


def _back_mode_choice_value(label):
    for v, lbl in BACK_MODE_CHOICES:
        if lbl == label:
            return v
    return 'groove'


def _fita_value_for(name, thin_name, thick_name):
    """Classify a stored tape name into a choice ('none'/'thin'/'thick') given the
    two configured tape names. An unknown non-empty tape counts as present, so it
    maps to 'thick' rather than being silently dropped."""
    if not name:
        return 'none'
    if name == thin_name:
        return 'thin'
    if name == thick_name:
        return 'thick'
    return 'thick'


# Default cabinet configuration (mm). The New Cabinet dialog opens with these;
# Edit Cabinet loads the stored config of the chosen cabinet instead.
#
# Toe kick (rodapé): built as a SEPARATE box the carcass rests on, not carved
# from the cabinet sides — standard Brazilian marcenaria practice, and it keeps
# every part flat/rectangular so cutting and assembly stay simple. Defaults
# follow the classic rule of thumb: 100mm high, 75mm front setback. Bases wider
# than 'toe_kick_max_span' get evenly spaced reinforcements (reforços). The
# cabinet's overall Altura (H) INCLUDES the base; the carcass box is H minus the
# kick height.
DEFAULT_CFG = {
    'W': 800.0, 'H': 2100.0, 'D': 400.0, 't': 18.0,
    'n_shelves': 3, 'material': MATERIALS[0][0],
    # Shelf depth: False (default) = shallower shelves recessed from the front by
    # 'shelf_front_setback' (30mm); True = shelves flush with the carcass front.
    # Either way an inset door still forces the shelves behind the door body.
    'shelf_align_front': False,
    # Back panel: 'back_mode' picks how it is mounted -- 'groove' (default) seats
    # it in dado grooves cut into the sides/base/top; 'overlay' applies a
    # full-width panel to the rear of the carcass (sobreposto), with no grooves.
    'with_back': True, 'back_mode': 'groove', 'back_material': 'MDF 6mm Cru',
    'back_t': 6.0, 'dado_depth': 8.0, 'back_setback': 10.0,
    'with_toe_kick': True, 'toe_kick_material': MATERIALS[0][0],
    'toe_kick_t': 18.0, 'toe_kick_height': 100.0, 'toe_kick_setback': 75.0,
    'toe_kick_max_span': 500.0,
    # Doors (portas): frameless doors on the carcass front. N doors span the
    # width, separated by an even reveal gap (folga) and edge-banded all round.
    # Overlay (sobreposta, default): doors sit forward of the front face, inset
    # from the carcass edges by the gap. Inset (embutida): doors sit inside the
    # opening, flush with the front face, with the gap as the reveal all round.
    'with_doors': False, 'door_material': MATERIALS[0][0],
    'door_t': 18.0, 'n_doors': 2, 'door_gap': 3.0, 'door_inset': False,
    'door_band': 'Fita PVC 1mm Branco',
    # Concealed-hinge boring: cup bores in the doors + mounting-plate pilot holes
    # in the adjacent side panels. Model-only (not carried by the CorteCloud CSV,
    # which has no furação field) — the hinge count is noted in Complemento.
    # 'hinge' carries the boring dimensions (see HINGE); tune per cabinet in
    # Advanced. Screw pilot specifics stay at the HINGE defaults (not in the UI).
    'with_hinges': True,
    'hinge': dict(HINGE),
    # Drawers (gavetas): a single column of N stacked drawers across the carcass
    # front. Each is a full BR-standard box + a face (overlay/inset like doors).
    # The slide hardware is chosen from the bundled manifest by key; its mounting
    # clearances come from that spec, so switching slides re-sizes the boxes
    # automatically — a side-mounted runner (roldana/telescópica) eats ~12,7mm of
    # clear width per side, an undermount (oculta) ~6,5mm plus a gap under the box.
    # cfg['slide'] can override those numbers per cabinet (see SLIDE). A lightweight
    # proxy always represents the slide; 'insert_real_hardware' additionally imports
    # the bundled CAD model when the chosen slide ships one.
    'with_drawers': False, 'n_drawers': 3, 'drawer_inset': False, 'drawer_gap': 3.0,
    'slide_key': 'telescopica_h45_350', 'insert_real_hardware': False,
    'drawer': dict(DRAWER),
    'slide': dict(SLIDE),
    'tol': dict(DEFAULT_TOL),
    # Edge banding (fita). Auto-bands the visible front edge of carcass parts and
    # all four edges of doors/faces; see FITA / fita_tape. Deep-merged in
    # normalize_cfg. The legacy 'door_band'/'drawer.face_band' keys are ignored on
    # build now that fronts source their tape from this block.
    'fita': dict(FITA),
    # Side<->base/top joinery (Fixacao Lateral). Both 'aligned' = today's
    # geometry (base/top between full-height sides). See JOINERY / build_cabinet.
    'joinery': dict(JOINERY),
    # Tamponamento (acabamento): applied finish panels over exposed faces (left /
    # right side + top). Adds to the envelope; interior unaffected. See TAMPONAMENTO.
    'tamponamento': dict(TAMPONAMENTO),
    # Arremate (ajuste): scribe / gap-filler pieces to reach an uncertain ceiling
    # (top sanefa) and/or side walls (front reguas). Adds to the envelope; interior
    # unaffected. See ARREMATE.
    'arremate': dict(ARREMATE),
    # Puxador integrado (frente estendida): handleless fronts that overhang the
    # cabinet at the bottom (or top) so the protruding edge is the grip. Off by
    # default; overlay fronts only. See PUXADOR / resolve_grip_size.
    'puxador': dict(PUXADOR),
    # Interior LAYOUT. None is a sentinel meaning "derive a single-region layout
    # from the flat fields above" (see normalize_cfg / _synthesize_layout_from_flat)
    # so old stored cabinets and the classic New/Edit dialog keep working. The
    # Cabinet Layout palette writes an explicit recursive region tree here: a node
    # is either a SPLIT {'split':'v'|'h','children':[{size,fixed,node},...]} or a
    # LEAF {'type':'open'|'shelves'|'doors'|'drawers', count, inset, ...}. When a
    # layout is present it is authoritative for the build. See build_region.
    'layout': None,
    # Per-panel edge-banding (fita) overrides, keyed by the panel's stable slot
    # name (e.g. 'Base', 'Prateleira 1', 'Gaveta 2 Frente'). Each value is a dict
    # of the four {fita_C1,fita_C2,fita_L1,fita_L2} tape names. Written by Edit
    # Panel and re-applied on every rebuild so per-body tape choices survive the
    # delete-and-rebuild edit flow (see _apply_panel_override). Empty by default.
    'panel_overrides': {},
    # Stable per-cabinet prefix for the Fusion User Parameters this cabinet
    # publishes (fmob_<prefix>_W, ...). Assigned once at first build and reused so
    # rebuilds update the same named params instead of colliding across cabinets.
    'param_prefix': None,
}

# Icon resources live next to this script (resources/<name>/16x16.png + 32x32.png).
RES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources')


def res(name):
    """Resource folder for a command's icons, or '' if it doesn't exist."""
    path = os.path.join(RES_DIR, name)
    return path if os.path.isdir(path) else ''


# -----------------------------------------------------------------------------
# Hardware library (drawer slides). A JSON manifest under resources/hardware/
# maps a slide key -> spec + optional CAD model file. Specs are in mm. Slide
# mounting clearances live here (not in the cabinet config) so switching slides
# re-sizes the drawer boxes automatically — the hardware analogue of how the
# HINGE defaults fill in the boring dimensions.
#
# Two MOUNTING families are modelled, because they eat the cabinet's clear width
# very differently:
#   'side'       — corrediça montada na LATERAL da gaveta (roldana / telescópica).
#                  The runner sits BETWEEN the carcass side and the box side, so it
#                  costs real width: side_space per side (~12,5mm roldana, ~12,7mm
#                  telescópica 45mm) and the box is only that much narrower.
#   'undermount' — corrediça OCULTA, under the box. Costs little width (~6,5mm per
#                  side) but needs bottom_clearance under the box.
# Both are expressed through one number the builder reads — side_space, the space
# per side between the carcass and the OUTER box side (see slide_side_space).
# carcass_deduction is the alternative convention used by undermount datasheets
# (Blum/Häfele: drawer bottom BETWEEN the sides = clear width - deduction); it is
# only read when side_space is absent.
#
# NOTE: the seeded clearances are the usual BR market figures (and best-guess
# values for the Häfele Matrix Invisa A30); confirm against the datasheet. They are
# pure data — editing hardware.json is enough, no code change — and every number
# is also overridable per cabinet via cfg['slide'] (see SLIDE).
# -----------------------------------------------------------------------------
HARDWARE_DIR = os.path.join(RES_DIR, 'hardware')

# Used when hardware.json is missing or a key can't be found, so drawer creation
# never breaks on a packaging/typo issue. Kept in sync with the seeded manifest.
FALLBACK_SLIDE = {
    'key': 'hafele_matrix_invisa_a30_300',
    'description': 'Corredica oculta Hafele Matrix Invisa A30 GT2 300mm (ext. total, push)',
    'type': 'undermount',
    'mount': 'undermount',        # 'undermount' (oculta) | 'side' (lateral)
    'nominal_length_mm': 300.0,   # NL
    # Undermount planning rule (Hafele): the drawer bottom BETWEEN the sides =
    # clear carcass width - carcass_deduction; the outer box width follows from
    # the side thickness. So the drawer is sized off this deduction, not a raw
    # side gap. (Datasheet: max drawer width = clear width - 42 + 2*side_t.)
    'carcass_deduction': 42.0,
    'side_space': None,           # space per side carcass <-> outer box side (mm);
                                  # None = derive it from carcass_deduction
    'side_panel_thickness': 16.0, # thickness the runner is designed for (info)
    'side_clearance': 6.0,        # resulting per-side air gap with 15mm sides (info)
    'bottom_clearance': 13.0,     # gap under the box for the undermount mechanism
    'back_clearance': 18.0,       # gap box back <-> carcass back (rear coupling room)
    'min_cabinet_depth': 318.0,   # required internal depth for the 300mm NL
    'recommended_box_depth': 300.0,  # box side length ~ NL
    'base_depth_offset': 29.0,    # base panel depth = NL - 29 (info)
    'profile_z_offset': 0.0,      # box floor -> bottom of the side-mounted profile
    'proxy_L': 300.0, 'proxy_W': 12.0, 'proxy_H': 45.0,   # slide envelope box (mm)
    'drilling': [],               # optional box-local pilots [{x,y,z,dia,depth}]
    'model_file': 'models/433_03_132_4.stp',              # rel to HARDWARE_DIR; '' = none
    'model_transform': {'tx': 0.0, 'ty': 0.0, 'tz': 0.0,
                        'rot_deg': 90.0, 'rot_axis': [0.0, 0.0, 1.0], 'scale': 1.0},
}

_HW_CACHE = None


def load_hardware_manifest():
    """The parsed hardware.json (cached). Degrades to an empty manifest if the
    file is missing/invalid, so resolve_slide_spec always falls back cleanly."""
    global _HW_CACHE
    if _HW_CACHE is None:
        try:
            with open(os.path.join(HARDWARE_DIR, 'hardware.json'), 'r', encoding='utf-8') as f:
                _HW_CACHE = json.load(f)
        except Exception:
            _HW_CACHE = {'slides': {}, 'default': ''}
    return _HW_CACHE


def resolve_slide_spec(cfg):
    """Full slide spec for cfg['slide_key'], backfilling any missing field from
    FALLBACK_SLIDE and then applying cfg['slide'] when it is marked 'custom'.
    Never raises — an unknown key falls back to the manifest default, then to
    FALLBACK_SLIDE, and a garbage override value is skipped."""
    man = load_hardware_manifest()
    slides = man.get('slides', {}) if isinstance(man, dict) else {}
    key = cfg.get('slide_key') or man.get('default') or FALLBACK_SLIDE['key']
    spec = dict(FALLBACK_SLIDE)
    if isinstance(slides.get(key), dict):
        spec.update(slides[key])
    spec['key'] = key
    ov = cfg.get('slide')
    if isinstance(ov, dict) and ov.get('custom'):
        for src, dst in _SLIDE_OVERRIDE_FIELDS:
            val = ov.get(src)
            if val is None or val == '':
                continue
            try:
                spec[dst] = float(val)
            except (TypeError, ValueError):
                pass
        spec['custom'] = True
        # An explicit per-side space always wins over the datasheet deduction.
        if spec.get('side_space') is not None:
            spec['carcass_deduction'] = None
    return spec


def slide_mount(spec):
    """'side' (lateral: roldana/telescópica) or 'undermount' (oculta)."""
    return 'side' if spec.get('mount') == 'side' else 'undermount'


def slide_side_space(spec, box_t_mm):
    """Space (mm) the slide takes on EACH side, between the carcass and the outer
    drawer-box side — the single number the box width is derived from.

    Side-mounted runners publish it directly (side_space). Undermount datasheets
    instead publish a carcass deduction on the bottom BETWEEN the sides, so the
    equivalent per-side space depends on the box wall thickness:
        bottom = clear - deduction  =>  outer = clear - deduction + 2*box_t
    """
    ss = spec.get('side_space')
    if ss is not None and ss != '':
        return float(ss)
    ded = spec.get('carcass_deduction')
    if ded is None:
        ded = 2.0 * spec.get('side_clearance', 0.0)
    return float(ded) / 2.0 - box_t_mm


def slide_box_outer_width(clear_w_mm, spec, box_t_mm):
    """Outer width (mm) of a drawer box in a clear opening, for this slide."""
    return clear_w_mm - 2.0 * slide_side_space(spec, box_t_mm)


def slide_proxy_slots(spec, box_x0_c, box_outer_w_c, box_y0_c, box_z0_c, box_h_c,
                      side_space_c, depth_c):
    """Where the two slide proxies/models sit for one drawer, as
    [(side, (x0, y0, z0, dx, dy, dz))] in cm — the one place the mounting family
    changes the model. Side-mounted runners fill the air gap beside each box side
    (a plate of side_space thickness, proxy_H tall); undermount runners sit in the
    runner gap directly under the box, hugging each side."""
    L_c = min(spec.get('proxy_L', 300.0) / 10.0, depth_c)
    if slide_mount(spec) == 'side':
        h_c = min(spec.get('proxy_H', 45.0) / 10.0, box_h_c)
        z0 = box_z0_c + min(spec.get('profile_z_offset', 0.0) / 10.0,
                            max(0.0, box_h_c - h_c))
        return [('E', (box_x0_c - side_space_c, box_y0_c, z0, side_space_c, L_c, h_c)),
                ('D', (box_x0_c + box_outer_w_c, box_y0_c, z0, side_space_c, L_c, h_c))]
    w_c = spec.get('proxy_W', 12.0) / 10.0
    bc_c = spec.get('bottom_clearance', 13.0) / 10.0
    z0 = box_z0_c - bc_c
    return [('E', (box_x0_c, box_y0_c, z0, w_c, L_c, bc_c)),
            ('D', (box_x0_c + box_outer_w_c - w_c, box_y0_c, z0, w_c, L_c, bc_c))]


def slide_keys():
    """Ordered [(key, description)] for the dropdown; always non-empty."""
    man = load_hardware_manifest()
    slides = man.get('slides', {}) if isinstance(man, dict) else {}
    items = [(k, v.get('description', k)) for k, v in slides.items()]
    return items or [(FALLBACK_SLIDE['key'], FALLBACK_SLIDE['description'])]


def slide_specs_for_ui():
    """The library as [{key, desc, mount, side_space, bottom_clearance,
    back_clearance, box_depth, min_cabinet_depth}], so the palettes can refill the
    'personalizar' fields when the user picks another slide."""
    out = []
    for k, desc in slide_keys():
        spec = resolve_slide_spec({'slide_key': k})
        out.append({
            'key': k, 'desc': desc, 'mount': slide_mount(spec),
            # Reported for a 16mm box wall, the reference the UI seeds from.
            'side_space': round(slide_side_space(spec, DRAWER['box_t']), 2),
            'bottom_clearance': spec.get('bottom_clearance', 0.0),
            'back_clearance': spec.get('back_clearance', 0.0),
            'box_depth': spec.get('recommended_box_depth', 0.0),
            'min_cabinet_depth': spec.get('min_cabinet_depth', 0.0),
        })
    return out


# -----------------------------------------------------------------------------
# User preferences (cross-document, cross-session)
#
# Fusion has no application-level settings store for add-ins (only per-document
# entity attributes, used elsewhere for panelData/cabinetConfig). So preferences
# live in a JSON file under a WRITABLE per-user location — NOT under RES_DIR,
# which is the install folder (read-only, wiped on add-in update). Loaded lazily
# and cached, degrading to an empty dict on any error exactly like
# load_hardware_manifest(), so a missing/corrupt file never breaks the add-in.
#
# Shape (v2 — several named PROFILES, one active):
#   {"version": 2,
#    "active_profile": "Padrão",
#    "profiles": [
#      {"name": "Padrão",
#       "materials": [{"name": "MDF 18mm Branco", "thickness": 18.0}, ...],
#       "cabinet_defaults": { partial cfg overriding DEFAULT_CFG: W,H,D,t, fita{},
#                             hinge{}, drawer{}, slide{}, slide_key, toe_kick*, ... }},
#      ...]}
#
# A profile is one complete "variation" of the configuration (a shop standard, a
# client, a job): switching the active profile swaps the material library AND the
# cabinet defaults together. The same document shape is what the Preferences
# palette exports/imports (plus a 'kind' marker), so a profile travels between
# machines as a plain .json file.
#
# v1 files ({version:1, materials, cabinet_defaults} — no profiles) are migrated
# on load into a single profile, so nothing is lost on upgrade; the migrated shape
# is only written back on the next save.
#
# The two accessors get_materials() / effective_default_cfg() are the single
# seams the rest of the add-in reads through, so saved prefs override the
# hardcoded MATERIALS / DEFAULT_CFG without changing any call site's shape.
PREFS_VERSION = 2
PREFS_FILE_KIND = 'fusionmob-preferences'    # marker in exported files
DEFAULT_PROFILE_NAME = 'Padrão'
_PREFS_CACHE = None


def prefs_path():
    """Absolute path to the per-user preferences.json (writable location)."""
    appdata = os.environ.get('APPDATA')
    if appdata:  # Windows
        base = os.path.join(appdata, 'FusionMob')
    else:        # Mac / other
        base = os.path.join(os.path.expanduser('~/Library/Application Support'), 'FusionMob')
    return os.path.join(base, 'preferences.json')


def _clean_stored_materials(raw):
    """Coerce a stored materials list into [{'name','thickness'}] (dropping junk)."""
    out = []
    if isinstance(raw, list):
        for m in raw:
            if isinstance(m, dict) and m.get('name'):
                try:
                    thk = float(m.get('thickness') or 0.0)
                except (TypeError, ValueError):
                    thk = 0.0
                out.append({'name': str(m['name']), 'thickness': thk})
    return out


def _clean_profile(raw, fallback_name):
    """Coerce a raw profile dict into {'name','materials','cabinet_defaults'}."""
    raw = raw if isinstance(raw, dict) else {}
    name = str(raw.get('name') or '').strip() or fallback_name
    defaults = raw.get('cabinet_defaults')
    return {'name': name,
            'materials': _clean_stored_materials(raw.get('materials')),
            'cabinet_defaults': dict(defaults) if isinstance(defaults, dict) else {}}


def _empty_store():
    return {'version': PREFS_VERSION, 'active_profile': DEFAULT_PROFILE_NAME, 'profiles': []}


def migrate_prefs(data, default_name=DEFAULT_PROFILE_NAME):
    """Normalize any preferences document (v1 flat, v2 profiles, or garbage) into
    the current store shape. Pure — also used to read imported files."""
    if not isinstance(data, dict):
        return _empty_store()
    raw_profiles = data.get('profiles')
    profiles, seen = [], set()
    if isinstance(raw_profiles, list):
        for i, p in enumerate(raw_profiles):
            prof = _clean_profile(p, '{} {}'.format(default_name, i + 1))
            key = prof['name'].lower()
            if key in seen:                       # de-duplicate names within a file
                prof['name'] = _unique_name(prof['name'], seen)
                key = prof['name'].lower()
            seen.add(key)
            profiles.append(prof)
    elif data.get('materials') is not None or data.get('cabinet_defaults') is not None:
        # v1: one implicit profile stored flat at the top level.
        profiles.append(_clean_profile(data, default_name))
    active = str(data.get('active_profile') or '').strip()
    if not any(p['name'] == active for p in profiles):
        active = profiles[0]['name'] if profiles else default_name
    return {'version': PREFS_VERSION, 'active_profile': active, 'profiles': profiles}


def _unique_name(base, taken_lower):
    """`base`, else 'base (2)', 'base (3)'… — first not in `taken_lower` (a set of
    lowercased names)."""
    if base.lower() not in taken_lower:
        return base
    n = 2
    while '{} ({})'.format(base, n).lower() in taken_lower:
        n += 1
    return '{} ({})'.format(base, n)


def load_preferences():
    """The migrated preferences store (cached). Degrades to an empty store if the
    file is missing/invalid, so the accessors fall back to the hardcoded defaults."""
    global _PREFS_CACHE
    if _PREFS_CACHE is None:
        try:
            with open(prefs_path(), 'r', encoding='utf-8') as f:
                data = json.load(f)
            _PREFS_CACHE = migrate_prefs(data)
        except Exception:
            _PREFS_CACHE = _empty_store()
    return _PREFS_CACHE


def save_preferences(store):
    """Write preferences.json (creating its folder) and invalidate the cache so
    the next dialog/palette sees the change. Raises on I/O error (caller reports)."""
    global _PREFS_CACHE
    path = prefs_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    _PREFS_CACHE = None


def clear_preferences():
    """Remove the preferences file (reset every profile to factory) and invalidate
    the cache."""
    global _PREFS_CACHE
    try:
        os.remove(prefs_path())
    except OSError:
        pass
    _PREFS_CACHE = None


def profile_names():
    """Names of the saved profiles, in stored order (may be empty)."""
    return [p['name'] for p in load_preferences()['profiles']]


def active_profile():
    """The active profile dict. Always returns the {'name','materials',
    'cabinet_defaults'} shape — an empty one when nothing is saved yet, so the
    accessors below fall back to the factory values."""
    store = load_preferences()
    for p in store['profiles']:
        if p['name'] == store['active_profile']:
            return p
    return {'name': store['active_profile'], 'materials': [], 'cabinet_defaults': {}}


def get_materials():
    """The active material library as [(name, thickness_mm), ...]: the active
    profile's list when present and non-empty, else the built-in MATERIALS seed.
    Thickness is informational (CorteCloud reads it from the name); part geometry
    uses the per-part cfg thickness, not this value."""
    saved = [(m['name'], m['thickness']) for m in active_profile()['materials'] if m.get('name')]
    return saved or list(MATERIALS)


def effective_default_cfg():
    """Factory DEFAULT_CFG with the active profile's cabinet defaults merged on top.
    normalize_cfg already deep-merges a partial cfg over DEFAULT_CFG (top level +
    tol/hinge/drawer/slide/fita), so a saved partial produces the right effective cfg;
    an absent/empty profile yields the plain normalized factory defaults."""
    return normalize_cfg(active_profile()['cabinet_defaults'])


def _slide_key_from_label(label):
    """Map a dropdown label (description) back to its slide key."""
    for k, desc in slide_keys():
        if desc == label:
            return k
    return slide_keys()[0][0]


def _slide_label_for_key(key):
    """Map a slide key to its dropdown label (description)."""
    for k, desc in slide_keys():
        if k == key:
            return desc
    return slide_keys()[0][1]

# CSV column separator. pt-BR spreadsheets default to ';'.
CSV_DELIMITER = ';'

# Exact CorteCloud import header (order matters).
CSV_HEADER = [
    'Quantidade', 'Comprimento', 'Largura', 'Funcao',
    'Fita C1', 'Fita C2', 'Fita L1', 'Fita L2',
    'Material', 'Complemento', 'Girar',
]


# -----------------------------------------------------------------------------
# Geometry + data helpers
# -----------------------------------------------------------------------------
def get_design():
    product = app.activeProduct
    if not isinstance(product, adsk.fusion.Design):
        return None
    return product


class PartDesignNotSupportedError(Exception):
    """Raised when the active document is a Part design (single-component only)."""
    pass


PART_DESIGN_MESSAGE = (
    'This document is a Part design, which can only contain one component.\n\n'
    'FusionMob needs to create multiple components (one per panel), so please '
    'create/open this in an Assembly or Hybrid design instead.'
)


def _add_new_component(parent_comp, transform):
    """Wrap occurrences.addNewComponent, translating Fusion's Part-design
    single-component limitation into a friendly error."""
    try:
        return parent_comp.occurrences.addNewComponent(transform)
    except RuntimeError as e:
        if 'Part Design documents can only contain one component' in str(e):
            raise PartDesignNotSupportedError(PART_DESIGN_MESSAGE)
        raise


def create_panel(design, data, largura_cm, comprimento_cm, thk_cm):
    """Create one panel body in its own component and tag it with JSON.

    Largura runs along X, Comprimento along Y, thickness along Z."""
    root = design.rootComponent

    # Offset each new panel along X so they don't stack on top of each other.
    idx = root.occurrences.count
    transform = adsk.core.Matrix3D.create()
    transform.translation = adsk.core.Vector3D.create((largura_cm + 5.0) * idx, 0.0, 0.0)
    occ = _add_new_component(root, transform)
    comp = occ.component
    comp.name = data['complemento'] or data['funcao'] or 'Panel'

    sketch = comp.sketches.add(comp.xYConstructionPlane)
    sketch.sketchCurves.sketchLines.addTwoPointRectangle(
        adsk.core.Point3D.create(0.0, 0.0, 0.0),
        adsk.core.Point3D.create(largura_cm, comprimento_cm, 0.0),
    )

    prof = sketch.profiles.item(0)
    extrudes = comp.features.extrudeFeatures
    ext_input = extrudes.createInput(prof, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    ext_input.setDistanceExtent(False, adsk.core.ValueInput.createByReal(thk_cm))
    ext = extrudes.add(ext_input)

    body = ext.bodies.item(0)
    body.name = comp.name
    body.attributes.add(ATTR_GROUP, ATTR_NAME, json.dumps(data))
    return body


def _make_temp_box(tbm, x0, y0, z0, dx, dy, dz):
    """A temporary axis-aligned BRep box spanning [x0,x0+dx] x [y0,..] x [z0,..] (cm)."""
    center = adsk.core.Point3D.create(x0 + dx / 2.0, y0 + dy / 2.0, z0 + dz / 2.0)
    obb = adsk.core.OrientedBoundingBox3D.create(
        center,
        adsk.core.Vector3D.create(1.0, 0.0, 0.0),
        adsk.core.Vector3D.create(0.0, 1.0, 0.0),
        dx, dy, dz)
    return tbm.createBox(obb)


def _make_temp_cylinder(tbm, x0, y0, z0, x1, y1, z1, radius):
    """A temporary cylinder spanning (x0,y0,z0)->(x1,y1,z1) with the given radius
    (all cm). Used as a boolean tool for hinge cup bores and screw pilot holes."""
    p0 = adsk.core.Point3D.create(x0, y0, z0)
    p1 = adsk.core.Point3D.create(x1, y1, z1)
    return tbm.createCylinderOrCone(p0, radius, p1, radius)


# Per-panel edge-banding overrides for the cabinet currently being built, keyed
# by panel slot name. Set by build_cabinet from cfg['panel_overrides'] and read
# by add_solid_panel/add_solid_body so a rebuild re-applies the tape choices Edit
# Panel made on individual bodies. Empty outside a build.
_ACTIVE_PANEL_OVERRIDES = {}


def _apply_panel_override(data, name, overrides):
    """Overwrite the four fita_* fields of a cut-list `data` dict in place from
    `overrides[name]` when present. `overrides` is cfg['panel_overrides'] (slot
    name -> {fita_C1,fita_C2,fita_L1,fita_L2}). No-op when there's no matching
    override or `data` is None. Returns `data`."""
    if not overrides or data is None:
        return data
    ov = overrides.get(name)
    if isinstance(ov, dict):
        for k in ('fita_C1', 'fita_C2', 'fita_L1', 'fita_L2'):
            if k in ov:
                data[k] = ov[k]
    return data


def add_solid_panel(cabinet_comp, name, box, data, grooves=None, holes=None):
    """Create a panel as an exact solid box (minus optional groove boxes and
    cylindrical holes) in its own component. `box` and each groove are
    (x0,y0,z0,dx,dy,dz) tuples in cm; each hole is (x0,y0,z0,x1,y1,z1,radius) cm.

    Uses TemporaryBRepManager so geometry lands exactly where specified, with no
    dependence on extrude start/extent interpretation. Returns the occurrence."""
    occ = new_part_component(cabinet_comp, name)
    comp = occ.component

    tbm = adsk.fusion.TemporaryBRepManager.get()
    body = _make_temp_box(tbm, *box)
    if grooves:
        for g in grooves:
            tool = _make_temp_box(tbm, *g)
            tbm.booleanOperation(body, tool, adsk.fusion.BooleanTypes.DifferenceBooleanType)
    if holes:
        for h in holes:
            tool = _make_temp_cylinder(tbm, *h)
            tbm.booleanOperation(body, tool, adsk.fusion.BooleanTypes.DifferenceBooleanType)

    base = comp.features.baseFeatures.add()
    base.startEdit()
    real = comp.bRepBodies.add(body, base)
    base.finishEdit()

    real.name = name
    _apply_panel_override(data, name, _ACTIVE_PANEL_OVERRIDES)
    real.attributes.add(ATTR_GROUP, ATTR_NAME, json.dumps(data))
    return occ


def make_panel_data(funcao, complemento, dim_a_mm, dim_b_mm, material, girar='Sim',
                    band=None, bands=None):
    """Build a CorteCloud cut-list record for one carcass panel.

    Comprimento is the larger face dimension, Largura the smaller; C1/C2 tape the
    two Comprimento edges, L1/L2 the two Largura edges.

    Edge banding, in priority order:
      * `band` (a tape name) — all four edges (the usual case for doors/faces).
      * `bands` — per-edge, expressed relative to the two dims the caller passes:
        {'a': (t, t), 'b': (t, t)} where 'a' is the pair of edges of length
        dim_a and 'b' the pair of length dim_b. They map to C1/C2/L1/L2 by the
        same larger->Comprimento rule, so a single front edge (e.g.
        bands={'a': (tape, '')}) always lands in the right column regardless of
        which dim is larger.
      * neither — all edges blank."""
    comp_mm = max(dim_a_mm, dim_b_mm)
    larg_mm = min(dim_a_mm, dim_b_mm)
    if band is not None:
        c1 = c2 = l1 = l2 = band or ''
    elif bands:
        a = tuple(bands.get('a', ('', '')))
        b = tuple(bands.get('b', ('', '')))
        # The dim_a pair are the Comprimento edges when dim_a is the larger.
        (c1, c2), (l1, l2) = (a, b) if dim_a_mm >= dim_b_mm else (b, a)
    else:
        c1 = c2 = l1 = l2 = ''
    return {
        'quantidade': 1,
        'comprimento_mm': round(comp_mm, 1),
        'largura_mm': round(larg_mm, 1),
        'funcao': funcao,
        'fita_C1': c1, 'fita_C2': c2, 'fita_L1': l1, 'fita_L2': l2,
        'material': material,
        'complemento': complemento,
        'girar': girar,
    }


def collect_panels(design):
    """Read every tagged panel definition back out of the document."""
    rows = []
    for attr in design.findAttributes(ATTR_GROUP, ATTR_NAME):
        if attr.value:
            try:
                rows.append(json.loads(attr.value))
            except (ValueError, TypeError):
                pass
    return rows


def write_cutlist_csv(path, rows):
    """Write the CorteCloud 'importar do Excel' cut list as CSV."""
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f, delimiter=CSV_DELIMITER)
        writer.writerow(CSV_HEADER)
        for r in rows:
            writer.writerow([
                r.get('quantidade', 1),
                r.get('comprimento_mm', ''),
                r.get('largura_mm', ''),
                r.get('funcao', ''),
                r.get('fita_C1', ''),
                r.get('fita_C2', ''),
                r.get('fita_L1', ''),
                r.get('fita_L2', ''),
                r.get('material', ''),
                r.get('complemento', ''),
                r.get('girar', 'Sim'),
            ])


# -----------------------------------------------------------------------------
# New Panel command
# -----------------------------------------------------------------------------
class NewPanelCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            inputs = args.command.commandInputs

            inputs.addStringValueInput('complemento', 'Complemento (label)', 'Lateral Esquerda')

            funcao = inputs.addDropDownCommandInput(
                'funcao', 'Funcao', adsk.core.DropDownStyles.TextListDropDownStyle)
            for i, name in enumerate(FUNCOES):
                funcao.listItems.add(name, i == 0)

            material = inputs.addDropDownCommandInput(
                'material', 'Material', adsk.core.DropDownStyles.TextListDropDownStyle)
            for i, (name, _thk) in enumerate(get_materials()):
                material.listItems.add(name, i == 0)

            # Finished dimensions. createByReal uses internal units (cm).
            inputs.addValueInput('comprimento', 'Comprimento', 'mm', adsk.core.ValueInput.createByReal(210.0))
            inputs.addValueInput('largura', 'Largura', 'mm', adsk.core.ValueInput.createByReal(80.0))
            inputs.addValueInput('thickness', 'Thickness (3D only)', 'mm', adsk.core.ValueInput.createByReal(1.8))
            inputs.addIntegerSpinnerCommandInput('qty', 'Quantidade', 1, 999, 1, 1)

            girar = inputs.addDropDownCommandInput(
                'girar', 'Girar (pode rotacionar)', adsk.core.DropDownStyles.TextListDropDownStyle)
            for i, name in enumerate(GIRAR_OPTIONS):
                girar.listItems.add(name, i == 0)

            # Edge banding: one tape name + a checkbox per edge.
            # C1/C2 = the two edges along the Comprimento; L1/L2 along the Largura.
            inputs.addStringValueInput('bandName', 'Fita (edge tape)', 'Fita PVC 1mm Branco')
            group = inputs.addGroupCommandInput('edges', 'Fita por borda')
            group.isExpanded = True
            ginputs = group.children
            ginputs.addBoolValueInput('edgeC1', 'Fita C1', True, '', False)
            ginputs.addBoolValueInput('edgeC2', 'Fita C2', True, '', False)
            ginputs.addBoolValueInput('edgeL1', 'Fita L1', True, '', False)
            ginputs.addBoolValueInput('edgeL2', 'Fita L2', True, '', False)

            execHandler = NewPanelExecuteHandler()
            args.command.execute.add(execHandler)
            handlers.append(execHandler)
        except:
            if ui:
                ui.messageBox('New Panel setup failed:\n{}'.format(traceback.format_exc()))


class NewPanelExecuteHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            design = get_design()
            if not design:
                ui.messageBox('Open a Design document first.')
                return

            inputs = args.command.commandInputs
            complemento = inputs.itemById('complemento').value
            funcao = inputs.itemById('funcao').selectedItem.name
            material = inputs.itemById('material').selectedItem.name
            girar = inputs.itemById('girar').selectedItem.name
            qty = inputs.itemById('qty').value
            band_name = inputs.itemById('bandName').value

            # Values come back in internal units (cm) -> convert to mm.
            comprimento_mm = inputs.itemById('comprimento').value * 10.0
            largura_mm = inputs.itemById('largura').value * 10.0
            thk_mm = inputs.itemById('thickness').value * 10.0

            c1 = inputs.itemById('edgeC1').value
            c2 = inputs.itemById('edgeC2').value
            l1 = inputs.itemById('edgeL1').value
            l2 = inputs.itemById('edgeL2').value

            data = {
                'quantidade': qty,
                'comprimento_mm': round(comprimento_mm, 1),
                'largura_mm': round(largura_mm, 1),
                'funcao': funcao,
                'fita_C1': band_name if c1 else '',
                'fita_C2': band_name if c2 else '',
                'fita_L1': band_name if l1 else '',
                'fita_L2': band_name if l2 else '',
                'material': material,
                'complemento': complemento,
                'girar': girar,
            }

            create_panel(design, data, largura_mm / 10.0, comprimento_mm / 10.0, thk_mm / 10.0)
        except PartDesignNotSupportedError as e:
            if ui:
                ui.messageBox(str(e))
        except:
            if ui:
                ui.messageBox('New Panel failed:\n{}'.format(traceback.format_exc()))


# -----------------------------------------------------------------------------
# New Cabinet command
#
# Frameless carcass: 2 sides (full height x depth), a base and a top captured
# between the sides, and N shelves evenly distributed in the interior opening.
# No back panel, no dados, no edge banding yet.
#
#   X = width (largura externa), Y = depth (profundidade), Z = height (altura)
# -----------------------------------------------------------------------------
def new_part_component(parent_comp, name):
    """Create an empty child component (identity transform) and return its occurrence."""
    occ = _add_new_component(parent_comp, adsk.core.Matrix3D.create())
    occ.component.name = name
    return occ


def assemble_with_joints(cabinet_comp, anchor_occ, other_occs):
    """Rigidly connect all panels so the cabinet behaves as one assembly.

    Tries as-built rigid joints first (each independently). If joints aren't
    available/complete, falls back to a Rigid Group, which is a more robust way
    to lock components together. Returns 'joints', 'rigidgroup', or 'none'.
    Grounding the anchor is a nice-to-have and never affects the result."""
    made = []
    try:
        as_built = cabinet_comp.asBuiltJoints
        for occ in other_occs:
            try:
                ji = as_built.createInput(anchor_occ, occ, None)
                ji.setAsRigidJointMotion()
                made.append(as_built.add(ji))
            except:
                pass
    except:
        pass

    if len(made) == len(other_occs) and made:
        try:
            anchor_occ.isGrounded = True
        except:
            pass
        return 'joints'

    # Joints failed/partial: undo any partial joints, then use a Rigid Group.
    for j in made:
        try:
            j.deleteMe()
        except:
            pass
    try:
        col = adsk.core.ObjectCollection.create()
        col.add(anchor_occ)
        for occ in other_occs:
            col.add(occ)
        cabinet_comp.rigidGroups.add(col, True)
        try:
            anchor_occ.isGrounded = True
        except:
            pass
        return 'rigidgroup'
    except:
        return 'none'


def _find_vertical_edge(body, x_c, y_c, tol=1e-3):
    """The straight, vertical (Z-running) edge of `body` located at (x_c, y_c) in
    the body's own coordinates (cm), or None. Used to locate a door's hinge line."""
    for edge in body.edges:
        g = edge.geometry
        if not isinstance(g, adsk.core.Line3D):
            continue
        sp = edge.startVertex.geometry
        ep = edge.endVertex.geometry
        if (abs(sp.x - ep.x) < tol and abs(sp.y - ep.y) < tol
                and abs(sp.z - ep.z) > tol):
            if abs(sp.x - x_c) < tol and abs(sp.y - y_c) < tol:
                return edge
    return None


def _set_door_swing_limits(joint, hinge_side):
    """Limit the swing to a ~110° range on the OUTWARD side so the pivot reads
    like a real hinge and can only open the correct way. The rest state stays at
    0 (closed). With the door as the joint's moving member (occurrenceOne) and
    both hinge edges running +Z, a positive rotation swings both doors the same
    rotational sense: that opens a LEFT-hinged door outward but a right-hinged
    one inward. Restricting the right door to the negative range (-110..0) makes
    it open outward instead. Optional; never fatal."""
    try:
        limits = joint.jointMotion.rotationLimits
        swing = math.radians(110.0)
        if hinge_side == 'left':
            lo, hi = 0.0, swing
        else:
            lo, hi = -swing, 0.0
        limits.isMinimumValueEnabled = True
        limits.minimumValue = lo
        limits.isMaximumValueEnabled = True
        limits.maximumValue = hi
    except:
        pass


def attach_door_pivots(cabinet_comp, anchor_occ, door_occs):
    """Give each door a vertical pivot (revolute) joint to the carcass so it can
    swing open. `door_occs` is a list of
    (name, occurrence, hinge_x_c, hinge_y_c, hinge_side): the hinge runs
    vertically at the door's interior-facing back face (y = hinge_y_c, which is 0
    for overlay doors and +thickness for inset ones), at x = hinge_x_c (cm) in the
    door body's coordinates; hinge_side ('left'/'right') sets which way it opens.
    The door is passed as the joint's occurrenceOne (the moving member) and the
    grounded carcass as occurrenceTwo (the fixed base), so opening swings the
    door and not the carcass. Best-effort — a door that can't be jointed is left
    positioned but free. Returns how many pivots were created."""
    made = 0
    try:
        as_built = cabinet_comp.asBuiltJoints
    except:
        return 0
    for _name, occ, hinge_x_c, hinge_y_c, hinge_side in door_occs:
        try:
            body = occ.component.bRepBodies.item(0)
            edge = _find_vertical_edge(body, hinge_x_c, hinge_y_c)
            if not edge:
                continue
            edge_proxy = edge.createForAssemblyContext(occ)
            geo = adsk.fusion.JointGeometry.createByCurve(
                edge_proxy, adsk.fusion.JointKeyPointTypes.MiddleKeyPoint)
            # Order matters: the joint value is occurrenceOne RELATIVE TO
            # occurrenceTwo, so occurrenceOne is the member that moves and
            # occurrenceTwo is the fixed base. The DOOR must be occurrenceOne
            # and the (grounded) carcass occurrenceTwo — otherwise opening the
            # door rotates the whole carcass instead of the door.
            ji = as_built.createInput(occ, anchor_occ, geo)
            ji.setAsRevoluteJointMotion(
                adsk.fusion.JointDirections.CustomJointDirection, edge_proxy)
            joint = as_built.add(ji)
            _set_door_swing_limits(joint, hinge_side)
            made += 1
        except:
            pass
    return made


# -----------------------------------------------------------------------------
# Drawer hardware (slides): a lightweight proxy box always represents the slide
# envelope; the real bundled CAD model is optionally imported alongside it.
# Neither carries a panelData attribute, so both are excluded from the cut list.
# -----------------------------------------------------------------------------
def add_solid_body(comp, name, box, data=None, grooves=None, holes=None):
    """Build an exact solid box (minus optional groove boxes and cylinder holes)
    as a body DIRECTLY inside `comp` — no new child component. Tags it with
    panelData when `data` is given (so it reaches the cut list); pass data=None
    for non-cut geometry (e.g. a slide proxy). Same cm tuple conventions as
    add_solid_panel. Returns the body. Used for drawers, where all of a drawer's
    parts live as bodies in one 'Gaveta N' component so it can take a real slider
    joint off its own geometry."""
    tbm = adsk.fusion.TemporaryBRepManager.get()
    body = _make_temp_box(tbm, *box)
    if grooves:
        for g in grooves:
            tbm.booleanOperation(body, _make_temp_box(tbm, *g),
                                 adsk.fusion.BooleanTypes.DifferenceBooleanType)
    if holes:
        for h in holes:
            tbm.booleanOperation(body, _make_temp_cylinder(tbm, *h),
                                 adsk.fusion.BooleanTypes.DifferenceBooleanType)
    base = comp.features.baseFeatures.add()
    base.startEdit()
    real = comp.bRepBodies.add(body, base)
    base.finishEdit()
    real.name = name
    if data is not None:
        _apply_panel_override(data, name, _ACTIVE_PANEL_OVERRIDES)
        real.attributes.add(ATTR_GROUP, ATTR_NAME, json.dumps(data))
    return real


def _find_horizontal_y_edge(body, x_c, z_c, tol=1e-3):
    """The straight edge of `body` running along Y (start/end differ only in Y)
    located at (x_c, z_c) in the assembly context (cm), or None. Defines a
    drawer's slide (prismatic) axis — the Y analogue of _find_vertical_edge."""
    for edge in body.edges:
        g = edge.geometry
        if not isinstance(g, adsk.core.Line3D):
            continue
        sp = edge.startVertex.geometry
        ep = edge.endVertex.geometry
        if (abs(sp.x - ep.x) < tol and abs(sp.z - ep.z) < tol
                and abs(sp.y - ep.y) > tol):
            if abs(sp.x - x_c) < tol and abs(sp.z - z_c) < tol:
                return edge
    return None


def _matrix_from_transform(xform, base_cm):
    """A Matrix3D that places an imported hardware model: optional uniform scale
    and rotation about rot_axis by rot_deg, then translation to base_cm (cm) plus
    the spec's tx/ty/tz (mm->cm). `xform` is the manifest 'model_transform' dict."""
    m = adsk.core.Matrix3D.create()
    xf = xform or {}
    scale = float(xf.get('scale', 1.0) or 1.0)
    if scale != 1.0:
        sm = adsk.core.Matrix3D.create()
        sm.setCell(0, 0, scale)
        sm.setCell(1, 1, scale)
        sm.setCell(2, 2, scale)
        m.transformBy(sm)
    rot_deg = float(xf.get('rot_deg', 0.0) or 0.0)
    if rot_deg:
        axis = xf.get('rot_axis') or [0.0, 0.0, 1.0]
        rm = adsk.core.Matrix3D.create()
        rm.setToRotation(math.radians(rot_deg),
                         adsk.core.Vector3D.create(axis[0], axis[1], axis[2]),
                         adsk.core.Point3D.create(0.0, 0.0, 0.0))
        m.transformBy(rm)
    bx, by, bz = base_cm
    m.translation = adsk.core.Vector3D.create(
        bx + float(xf.get('tx', 0.0)) / 10.0,
        by + float(xf.get('ty', 0.0)) / 10.0,
        bz + float(xf.get('tz', 0.0)) / 10.0)
    return m


def import_hardware_component(design, model_path, name):
    """Import an external hardware model ONCE and return an occurrence whose
    component the caller can instance/position, or None on any failure.

    CAD (.step/.stp/.f3d/.iges) is imported into the ROOT component via the
    ImportManager — importing into a freshly created child component can
    invalidate the in-progress assembly (`addNewComponent` then raises "refers to
    a deleted Object"), so we import at root and instance from there. Mesh
    (.obj/.stl/.3mf) goes into a dedicated child holder under root via meshBodies.
    The result carries no panelData, so hardware never reaches the cut list."""
    if not model_path or not os.path.isfile(model_path):
        return None
    ext = os.path.splitext(model_path)[1].lower()
    root = design.rootComponent

    if ext in ('.obj', '.stl', '.3mf'):
        occ = new_part_component(root, name)
        comp = occ.component
        try:
            base = comp.features.baseFeatures.add()
            base.startEdit()
            try:
                mb = comp.meshBodies
                unit = adsk.fusion.MeshUnits.MillimeterMeshUnit
                try:
                    mb.add(mb.createMeshImportOptions(model_path, unit))
                except Exception:
                    mb.add(model_path, unit)
            finally:
                base.finishEdit()
            return occ
        except Exception:
            try:
                occ.deleteMe()
            except Exception:
                pass
            return None

    try:
        im = app.importManager
        if ext in ('.step', '.stp'):
            opts = im.createSTEPImportOptions(model_path)
        elif ext == '.f3d':
            opts = im.createFusionArchiveImportOptions(model_path)
        elif ext in ('.igs', '.iges'):
            opts = im.createIGESImportOptions(model_path)
        else:
            return None
        try:
            opts.isViewFit = False
        except Exception:
            pass
        before = set()
        for o in root.occurrences:
            try:
                before.add(o.entityToken)
            except Exception:
                pass
        im.importToTarget(opts, root)
    except Exception:
        return None

    # importToTarget returns a Boolean, so locate the newly added occurrence.
    try:
        for o in root.occurrences:
            try:
                if o.entityToken not in before:
                    o.component.name = name
                    return o
            except Exception:
                continue
    except Exception:
        return None
    return None


def _rigid_group_occs(parent_comp, occ_list):
    """Lock a set of occurrences together with a Rigid Group in parent_comp's
    context. Best-effort; needs at least two occurrences."""
    try:
        occ_list = [o for o in occ_list if o is not None]
        if len(occ_list) < 2:
            return False
        col = adsk.core.ObjectCollection.create()
        for o in occ_list:
            col.add(o)
        parent_comp.rigidGroups.add(col, True)
        return True
    except Exception:
        return False


def _slide_forward_sign(joint, edge):
    """Which sign of the joint value moves the drawer toward the cabinet FRONT
    (−Y): +1 if the slide axis already points at −Y, −1 if it points at +Y.

    The axis comes from CustomJointDirection over a body edge, and an edge's
    direction is whatever the BRep hands back — the box sides are cut by a
    boolean difference (the bottom groove), which can flip the surviving edge —
    so it must be MEASURED, never assumed. The joint's own slideDirectionVector
    is authoritative (it is the direction positive values move occurrenceOne);
    the edge's start->end vector is the fallback. Cabinet occurrences carry a
    translation only (never a rotation), so the Y sign is the same in the
    assembly context and in cabinet-local coordinates. Defaults to +1."""
    try:
        v = joint.jointMotion.slideDirectionVector
        if abs(v.y) > 1e-9:
            return -1.0 if v.y > 0.0 else 1.0
    except Exception:
        pass
    try:
        dy = edge.endVertex.geometry.y - edge.startVertex.geometry.y
        if abs(dy) > 1e-9:
            return -1.0 if dy > 0.0 else 1.0
    except Exception:
        pass
    return 1.0


def _set_drawer_travel_limits(joint, travel_c, edge):
    """Limit the slide to ~full extension toward the FRONT, closed at rest (0).
    The allowed range sits on whichever side of 0 actually opens the drawer
    (see _slide_forward_sign): [0, +travel] when the axis points −Y, or
    [−travel, 0] when it points +Y. Pinning it to the positive side regardless
    is what used to make some drawers slide INTO the carcass instead of out.
    Optional; never fatal."""
    try:
        travel = abs(travel_c) * _slide_forward_sign(joint, edge)
        lo, hi = (0.0, travel) if travel >= 0.0 else (travel, 0.0)
        limits = joint.jointMotion.slideLimits
        limits.isMinimumValueEnabled = True
        limits.minimumValue = lo
        limits.isMaximumValueEnabled = True
        limits.maximumValue = hi
    except:
        pass


def attach_drawer_slides(cabinet_comp, anchor_occ, drawer_units):
    """Give each drawer (Gaveta) component a horizontal slider (prismatic) joint to
    the carcass so it pulls open along the cabinet depth (Y). Each unit is a dict
    {'occ', 'edge_x_c', 'edge_z_c', 'travel_c'}; the joint axis is a Y-running edge
    of the drawer's own box-side body (at edge_x_c/edge_z_c). As-built joints need
    real geometry for non-rigid motion (None only works for rigid joints), which is
    why we locate that edge. The drawer is the joint's occurrenceOne (the moving
    member) and the grounded carcass occurrenceTwo (the fixed base), so pulling
    slides the drawer and not the carcass. Best-effort — a drawer that can't be
    jointed is left positioned but free. Returns how many slides were created."""
    made = 0
    try:
        as_built = cabinet_comp.asBuiltJoints
    except Exception:
        return 0
    for unit in drawer_units:
        try:
            occ = unit['occ']
            edge = None
            for body in occ.bRepBodies:
                edge = _find_horizontal_y_edge(body, unit['edge_x_c'], unit['edge_z_c'])
                if edge:
                    break
            if not edge:
                continue
            geo = adsk.fusion.JointGeometry.createByCurve(
                edge, adsk.fusion.JointKeyPointTypes.MiddleKeyPoint)
            # occurrenceOne is the mover (value = occurrenceOne relative to
            # occurrenceTwo); the DRAWER must be occurrenceOne and the grounded
            # carcass occurrenceTwo, or pulling the drawer slides the carcass.
            ji = as_built.createInput(occ, anchor_occ, geo)
            ji.setAsSliderJointMotion(
                adsk.fusion.JointDirections.CustomJointDirection, edge)
            joint = as_built.add(ji)
            _set_drawer_travel_limits(joint, unit['travel_c'], edge)
            made += 1
        except Exception:
            pass
    return made


# -----------------------------------------------------------------------------
# Interior region grid: a pure planner turns the layout tree into a flat list of
# leaf bands + divider panels, and band-aware builders render each leaf. A "band"
# is the clear interior rectangle (cm) a leaf lives in, plus per-side overlay
# reach (ext_*, how far a sobreposta door/face covers the bounding panel) and the
# key of the vertical panel bounding it in X (so hinge plates bore into the right
# body). The single-region case reproduces the classic full-carcass geometry.
# -----------------------------------------------------------------------------
class _Band:
    __slots__ = ('x0', 'x1', 'z0', 'z1', 'ext_l', 'ext_r', 'ext_b', 'ext_t',
                 'left_key', 'right_key')

    def __init__(self, x0, x1, z0, z1, ext_l, ext_r, ext_b, ext_t, left_key, right_key):
        self.x0, self.x1, self.z0, self.z1 = x0, x1, z0, z1
        self.ext_l, self.ext_r, self.ext_b, self.ext_t = ext_l, ext_r, ext_b, ext_t
        self.left_key, self.right_key = left_key, right_key


class _BuildCtx(object):
    """Mutable shared state threaded through the region builders (set in
    build_cabinet). Accumulators: warnings, door_occs, drawer_bundles, hw_slots,
    hole_map (bound-key -> hinge plate holes), and the running part counters."""
    pass


def _next(counter):
    counter[0] += 1
    return counter[0]


def split_child_extents(L_mm, children, t_mm):
    """(offset_mm, size_mm) for each child along a split axis of clear length
    L_mm, with a t_mm divider between consecutive children. Fixed children take
    their size; flex children share the leftover by weight. Raises ValueError when
    it does not fit (surfaced by validate_cfg)."""
    k = len(children)
    avail = L_mm - (k - 1) * t_mm
    if avail <= 0:
        raise ValueError('Not enough room to divide this region into {0} parts '
                         '(the dividers alone need {1:.0f}mm).'.format(k, (k - 1) * t_mm))
    fixed_sum = sum(c['size'] for c in children if c['fixed'])
    flex = [c for c in children if not c['fixed']]
    flex_total = sum(c['size'] for c in flex) or 1.0
    leftover = avail - fixed_sum
    if fixed_sum > avail + 1e-6 or (flex and leftover <= 0):
        raise ValueError('The fixed region sizes ({0:.0f}mm) do not fit in the '
                         'available {1:.0f}mm.'.format(fixed_sum, avail))
    sizes = [c['size'] if c['fixed'] else leftover * c['size'] / flex_total
             for c in children]
    out, cur = [], 0.0
    for i, s in enumerate(sizes):
        out.append((cur, s))
        cur += s + (t_mm if i < k - 1 else 0.0)
    return out


def plan_layout(root_band, layout, t_c, div_depth_c):
    """Pure walk of the region tree. Returns (leaves, dividers):
      leaves   = [(band, leaf_node, prefix), ...]
      dividers = [{'orient','box','data_dims','funcao','name','key'}, ...]
    'v' dividers (from an 'h' split) are vertical and mountable (key = an int used
    to attach hinge plate holes); 'h' dividers (from a 'v' split) are horizontal
    (key None). Raises ValueError on an infeasible split."""
    leaves, dividers = [], []
    counter = [0]

    def rec(band, node, prefix):
        if not is_split(node):
            leaves.append((band, node, prefix))
            return
        axis = node['split']
        children = node['children']
        k = len(children)
        L_mm = ((band.x1 - band.x0) if axis == 'h' else (band.z1 - band.z0)) * 10.0
        extents = split_child_extents(L_mm, children, t_c * 10.0)
        div_ids = [_next(counter) for _ in range(k - 1)] if axis == 'h' else [None] * max(0, k - 1)
        for i, (ch, (off_mm, size_mm)) in enumerate(zip(children, extents)):
            off_c, size_c = off_mm / 10.0, size_mm / 10.0
            is_first, is_last = (i == 0), (i == k - 1)
            if axis == 'h':
                cb = _Band(band.x0 + off_c, band.x0 + off_c + size_c, band.z0, band.z1,
                           band.ext_l if is_first else t_c / 2.0,
                           band.ext_r if is_last else t_c / 2.0,
                           band.ext_b, band.ext_t,
                           band.left_key if is_first else div_ids[i - 1],
                           band.right_key if is_last else div_ids[i])
            else:
                cb = _Band(band.x0, band.x1, band.z0 + off_c, band.z0 + off_c + size_c,
                           band.ext_l, band.ext_r,
                           band.ext_b if is_first else t_c / 2.0,
                           band.ext_t if is_last else t_c / 2.0,
                           band.left_key, band.right_key)
            rec(cb, ch['node'], '{0}.{1}'.format(prefix, i + 1))
        for i in range(k - 1):
            off_mm, size_mm = extents[i]
            if axis == 'h':
                x_div = band.x0 + (off_mm + size_mm) / 10.0
                box = (x_div, 0.0, band.z0, t_c, div_depth_c, band.z1 - band.z0)
                dividers.append({'orient': 'v', 'box': box,
                                 'data_dims': ((band.z1 - band.z0) * 10.0, div_depth_c * 10.0),
                                 'funcao': 'Lateral',
                                 'name': 'Divisoria V {0}'.format(div_ids[i]),
                                 'key': div_ids[i]})
            else:
                z_div = band.z0 + (off_mm + size_mm) / 10.0
                box = (band.x0, 0.0, z_div, band.x1 - band.x0, div_depth_c, t_c)
                dividers.append({'orient': 'h', 'box': box,
                                 'data_dims': ((band.x1 - band.x0) * 10.0, div_depth_c * 10.0),
                                 'funcao': 'Prateleira',
                                 'name': 'Divisoria H {0}.{1}'.format(prefix, i + 1),
                                 'key': None})

    rec(root_band, layout, 'R')
    return leaves, dividers


def band_shelf_z_bottoms(z0_c, z1_c, t, n):
    """Z (cm) of each shelf bottom, evenly distributed in an ALREADY-CLEAR band of
    height (z1-z0). Returns [] when they don't fit. (Band analogue of
    shelf_z_bottoms, which subtracts 2t for the outer box.)"""
    if n <= 0:
        return []
    opening = (z1_c - z0_c) * 10.0
    gap = (opening - n * t) / (n + 1)
    if gap <= 0:
        return []
    gap_c, tc = gap / 10.0, t / 10.0
    return [z0_c + gap_c + i * (gap_c + tc) for i in range(n)]


def _pname(ctx, prefix, base, idx, single_ok=False):
    """Region-scoped part name. For a single-region cabinet, names match the
    classic ones (e.g. 'Prateleira 1', 'Porta'); multi-region parts get a region
    prefix so nothing collides in the browser or the cut list."""
    name = base if single_ok else '{0} {1}'.format(base, idx)
    if not ctx.single_leaf:
        name = '{0} {1}'.format(prefix, name)
    return name


def _resolve_leaf_slide(node, ctx):
    """Slide spec for a drawers leaf, honouring a per-leaf slide_key override. The
    cabinet's cfg['slide'] tweaks ride along so a customized spec applies to every
    region, whichever slide the leaf picked."""
    return resolve_slide_spec({'slide_key': node.get('slide_key') or ctx.slide_key,
                               'slide': ctx.slide})


def build_shelves(band, node, ctx, prefix, min_front_setback=0.0):
    """Evenly spaced shelves filling the band. `min_front_setback` lets a doors
    leaf push its shelves_behind back enough to clear a closed door."""
    n = node['count']
    if n <= 0:
        return
    align = node.get('shelf_align_front')
    if align is None:
        align = ctx.shelf_align_front_default
    front_setback = 0.0 if align else ctx.tol['shelf_front_setback']
    front_setback = max(front_setback, min_front_setback)
    if ctx.with_back:
        shelf_depth = ctx.back_front_y - ctx.tol['shelf_back_gap'] - front_setback
    else:
        shelf_depth = ctx.D - front_setback
    if shelf_depth <= 0:
        raise ValueError('Shelf depth is non-positive; reduce the back setback/gaps '
                         'or increase the cabinet depth.')
    zs = band_shelf_z_bottoms(band.z0, band.z1, ctx.t, n)
    if not zs:
        raise ValueError('Too many shelves for this region height.')
    width_c = band.x1 - band.x0
    for z0 in zs:
        ctx.shelf_i += 1
        name = _pname(ctx, prefix, 'Prateleira', ctx.shelf_i)
        ctx.add_panel(name,
                      (band.x0, front_setback / 10.0, z0, width_c, shelf_depth / 10.0, ctx.tc),
                      make_panel_data('Prateleira', name, width_c * 10.0, shelf_depth, ctx.material,
                                      bands={'a': (ctx.fita_carcass, '')}))


def build_doors(band, node, ctx, prefix):
    """Frameless doors filling the band (overlay or inset), with concealed-hinge
    cup bores and — for the outermost doors, whose hinge sits at a real vertical
    panel — mounting-plate pilots registered onto that panel via ctx.hole_map.

    A handleless cabinet (puxador integrado) makes the doors longer than the
    region: the lip hangs past the cabinet's bottom/top edge and is the grip. The
    hinges still spread over the CARCASS-covered part of the door, so the plate
    pilots stay inside the side panel."""
    n = node['count']
    gap = ctx.door_gap if node.get('gap') is None else node['gap']
    gap_c = gap / 10.0
    inset = node.get('inset', False)
    door_t = ctx.door_t
    dt_c = door_t / 10.0
    hinge = ctx.hinge
    with_hinges = ctx.with_hinges

    if inset:
        span0_c = band.x0
        region_w_mm = (band.x1 - band.x0) * 10.0
        door_z0 = band.z0 + gap_c
        region_h_mm = (band.z1 - band.z0) * 10.0
        door_y0_c, door_back_c = 0.0, dt_c
    else:
        span0_c = band.x0 - band.ext_l
        region_w_mm = ((band.x1 + band.ext_r) - (band.x0 - band.ext_l)) * 10.0
        z_bottom_outer = band.z0 - band.ext_b
        door_z0 = z_bottom_outer + gap_c
        region_h_mm = ((band.z1 + band.ext_t) - (band.z0 - band.ext_b)) * 10.0
        door_y0_c, door_back_c = -dt_c, 0.0

    # Handleless grip lip (puxador integrado): grow the door past the cabinet edge.
    # The hinge layout keeps using the CORE (un-lipped) extent so the plate pilots
    # stay within the side panel.
    lip_b, lip_t = grip_lip_extents(ctx.puxador, ctx.grip_size, band,
                                    ctx.carcass_z0_c, ctx.carcass_z1_c, inset)
    core_z0, core_h_mm = door_z0, region_h_mm - 2 * gap
    door_z0 = core_z0 - lip_b
    door_h_mm = core_h_mm + (lip_b + lip_t) * 10.0
    door_w_mm = (region_w_mm - (n + 1) * gap) / n
    door_w_c = door_w_mm / 10.0
    door_h_c = door_h_mm / 10.0
    band_mid = (band.x0 + band.x1) / 2.0

    hinge_zs = []
    if with_hinges:
        hinge_zs = hinge_z_positions(core_h_mm, core_z0, hinge['end_inset'])
        sb = int(node.get('shelves_behind', 0) or 0)
        shelf_bottoms = band_shelf_z_bottoms(band.z0, band.z1, ctx.t, sb) if sb > 0 else []
        if shelf_bottoms:
            cup_rad_c = (hinge['cup_diameter'] / 2.0) / 10.0
            lo = core_z0 + cup_rad_c + 0.5
            hi = core_z0 + core_h_mm / 10.0 - cup_rad_c - 0.5
            hinge_zs, unresolved = resolve_hinge_conflicts(
                hinge_zs, shelf_bottoms, ctx.tc, lo, hi, hinge['shelf_clearance'] / 10.0)
            if unresolved:
                ctx.warnings.append(
                    '{0} hinge(s) could not be moved clear of a shelf and may clash '
                    'with one.'.format(len(unresolved)))
        scr_r = (hinge['screw_diameter'] / 2.0) / 10.0
        scr_d = min(hinge['screw_depth'], ctx.t - 2.0) / 10.0
        pf_c = hinge['plate_front'] / 10.0
        pitch_c = hinge['screw_pitch'] / 10.0
        eps = 0.01

        def _plate_holes(face_x, into_dir):
            x_out = face_x - into_dir * eps
            x_in = face_x + into_dir * scr_d
            return [(x_out, yy, z, x_in, yy, z, scr_r)
                    for z in hinge_zs for yy in (pf_c, pf_c + pitch_c)]

        first_center = span0_c + gap_c + door_w_c / 2.0
        last_center = span0_c + gap_c + (n - 1) * (door_w_c + gap_c) + door_w_c / 2.0
        if first_center <= band_mid:
            ctx.hole_map.setdefault(band.left_key, []).extend(_plate_holes(band.x0, -1.0))
        if last_center > band_mid:
            ctx.hole_map.setdefault(band.right_key, []).extend(_plate_holes(band.x1, 1.0))

    cup_r = (hinge['cup_diameter'] / 2.0) / 10.0
    cup_d = min(hinge['cup_depth'], door_t - 2.0) / 10.0
    edge_c = hinge['cup_edge'] / 10.0
    eps = 0.01
    lip_note = grip_note(lip_b, lip_t)
    for i in range(n):
        x0 = span0_c + gap_c + i * (door_w_c + gap_c)
        ctx.door_i += 1
        name = _pname(ctx, prefix, 'Porta', ctx.door_i, single_ok=(ctx.single_leaf and n == 1))
        data = make_panel_data('Porta', name, door_h_mm, door_w_mm,
                               ctx.door_material, girar='Nao', band=ctx.fita_front)
        door_center = x0 + door_w_c / 2.0
        if door_center <= band_mid:
            hinge_x_c, hinge_side = x0, 'left'
        else:
            hinge_x_c, hinge_side = x0 + door_w_c, 'right'
        cup_holes = None
        notes = []
        if with_hinges:
            cup_x = x0 + edge_c if hinge_side == 'left' else x0 + door_w_c - edge_c
            cup_holes = [(cup_x, door_back_c + eps, z, cup_x, door_back_c - cup_d, z, cup_r)
                         for z in hinge_zs]
            notes.append('{0}x dobradica caneco {1:.0f}mm'.format(
                len(hinge_zs), hinge['cup_diameter']))
        if lip_note:
            notes.append(lip_note)
        if notes:
            data['complemento'] = '{0} ({1})'.format(name, ', '.join(notes))
        d_occ = add_solid_panel(ctx.cabinet_comp, name,
                                (x0, door_y0_c, door_z0, door_w_c, dt_c, door_h_c),
                                data, holes=cup_holes)
        ctx.door_occs.append((name, d_occ, hinge_x_c, door_back_c, hinge_side))

    sb = int(node.get('shelves_behind', 0) or 0)
    if sb > 0:
        door_reach = (door_t if inset else 0.0) + ctx.tol['shelf_door_clearance']
        build_shelves(band, {'type': 'shelves', 'count': sb, 'shelf_align_front': None},
                      ctx, prefix, min_front_setback=door_reach)


def build_drawers(band, node, ctx, prefix):
    """A stack of N drawers filling the band: box (2 sides + front + back + a
    dadoed bottom) + a face, plus slide proxies/model slots. Box width derives
    from the band's clear width so slides fit whatever bounds the region.

    With a handleless cabinet (puxador integrado) only the face at the very
    bottom/top of the stack can carry the grip lip — the ones in the middle have
    a neighbouring face there — so that one face is cut longer and the others are
    untouched. The boxes never move: they are laid out against the carcass."""
    spec = _resolve_leaf_slide(node, ctx)
    drawer = ctx.drawer
    n = node['count']
    gap_mm = ctx.drawer_gap if node.get('gap') is None else node['gap']
    dg_c = gap_mm / 10.0
    box_t = drawer['box_t']
    bt_c = box_t / 10.0
    face_t = drawer['face_t']
    face_t_c = face_t / 10.0
    inset = node.get('inset', False)

    if inset:
        span0_c = band.x0
        region_w_mm = (band.x1 - band.x0) * 10.0
        region_h_mm = (band.z1 - band.z0) * 10.0
        z_region0 = band.z0
        face_y0_c, face_back_c = 0.0, face_t_c
    else:
        span0_c = band.x0 - band.ext_l
        region_w_mm = ((band.x1 + band.ext_r) - (band.x0 - band.ext_l)) * 10.0
        z_region0 = band.z0 - band.ext_b
        region_h_mm = ((band.z1 + band.ext_t) - (band.z0 - band.ext_b)) * 10.0
        face_y0_c, face_back_c = -face_t_c, 0.0

    face_w_mm = region_w_mm - 2 * gap_mm
    face_w_c = face_w_mm / 10.0
    face_x0_c = span0_c + dg_c
    face_h_mm = (region_h_mm - (n + 1) * gap_mm) / n
    face_h_c = face_h_mm / 10.0
    # Handleless grip lip (puxador integrado): only the outermost face of the
    # stack reaches the cabinet edge, so only that one is extended.
    lip_b, lip_t = grip_lip_extents(ctx.puxador, ctx.grip_size, band,
                                    ctx.carcass_z0_c, ctx.carcass_z1_c, inset)

    # The runner eats clear width: a side-mounted one sits beside each box side,
    # an undermount one only needs a small air gap (see slide_side_space).
    region_inner_w_mm = (band.x1 - band.x0) * 10.0
    side_space_mm = slide_side_space(spec, box_t)
    side_space_c = side_space_mm / 10.0
    box_outer_w_mm = region_inner_w_mm - 2 * side_space_mm
    box_outer_w_c = box_outer_w_mm / 10.0
    box_x0_c = band.x0 + (region_inner_w_mm / 10.0 - box_outer_w_c) / 2.0
    inner_bw_mm = box_outer_w_mm - 2 * box_t
    inner_bw_c = inner_bw_mm / 10.0

    back_front_y_mm = ctx.back_front_y if ctx.with_back else ctx.D
    box_depth_mm = min(spec['recommended_box_depth'], back_front_y_mm - spec['back_clearance'])
    box_depth_c = box_depth_mm / 10.0
    box_y0_c = face_back_c

    base_top_c = band.z0
    top_bot_c = band.z1
    bc_c = spec['bottom_clearance'] / 10.0
    top_gap_c = drawer['box_top_gap'] / 10.0
    box_max_h_c = drawer['box_height'] / 10.0

    bdd_c = drawer['bottom_dado_depth'] / 10.0
    bpt_c = drawer['bottom_t'] / 10.0
    bu_c = drawer['bottom_up'] / 10.0
    pl_c = drawer['bottom_play'] / 10.0
    gz0_off = bu_c - pl_c / 2.0
    gdz = bpt_c + pl_c
    bot_w_mm = inner_bw_mm + 2 * drawer['bottom_dado_depth']
    bot_d_mm = (box_depth_mm - 2 * box_t) + 2 * drawer['bottom_dado_depth']

    box_mat = drawer['box_material']

    for i in range(n):
        ctx.drawer_i += 1
        label = _pname(ctx, prefix, 'Gaveta', ctx.drawer_i)
        fz0 = z_region0 + dg_c + i * (face_h_c + dg_c)
        ftop = fz0 + face_h_c
        bz0 = max(fz0, base_top_c) + bc_c
        box_top_c = min(ftop, top_bot_c) - top_gap_c
        box_h_c = box_top_c - bz0
        if box_h_c > box_max_h_c:
            box_h_c = box_max_h_c
        if box_h_c <= 0:
            box_h_c = 1.0
        box_h_mm = box_h_c * 10.0
        back_h_mm = box_h_mm - drawer['back_height_reduction']
        back_h_c = back_h_mm / 10.0

        drawer_occ = new_part_component(ctx.cabinet_comp, label)
        dcomp = drawer_occ.component

        gz = bz0 + gz0_off
        left_gr = [(box_x0_c + bt_c - bdd_c, box_y0_c, gz, bdd_c, box_depth_c, gdz)]
        right_gr = [(box_x0_c + box_outer_w_c - bt_c, box_y0_c, gz, bdd_c, box_depth_c, gdz)]
        front_gr = [(box_x0_c + bt_c, box_y0_c + bt_c - bdd_c, gz, inner_bw_c, bdd_c, gdz)]
        back_gr = [(box_x0_c + bt_c, box_y0_c + box_depth_c - bt_c, gz, inner_bw_c, bdd_c, gdz)]

        add_solid_body(dcomp, label + ' Lateral E',
            (box_x0_c, box_y0_c, bz0, bt_c, box_depth_c, box_h_c),
            make_panel_data('Lateral', label + ' Lateral E', box_h_mm, box_depth_mm, box_mat), left_gr)
        add_solid_body(dcomp, label + ' Lateral D',
            (box_x0_c + box_outer_w_c - bt_c, box_y0_c, bz0, bt_c, box_depth_c, box_h_c),
            make_panel_data('Lateral', label + ' Lateral D', box_h_mm, box_depth_mm, box_mat), right_gr)
        add_solid_body(dcomp, label + ' Frente Caixa',
            (box_x0_c + bt_c, box_y0_c, bz0, inner_bw_c, bt_c, box_h_c),
            make_panel_data('Travessa', label + ' Frente Caixa', box_h_mm, inner_bw_mm, box_mat), front_gr)
        add_solid_body(dcomp, label + ' Fundo Caixa',
            (box_x0_c + bt_c, box_y0_c + box_depth_c - bt_c, bz0, inner_bw_c, bt_c, back_h_c),
            make_panel_data('Travessa', label + ' Fundo Caixa', back_h_mm, inner_bw_mm, box_mat), back_gr)
        add_solid_body(dcomp, label + ' Fundo',
            (box_x0_c + bt_c - bdd_c, box_y0_c + bt_c - bdd_c, bz0 + bu_c,
             bot_w_mm / 10.0, bot_d_mm / 10.0, bpt_c),
            make_panel_data('Fundo', label + ' Fundo', bot_w_mm, bot_d_mm, drawer['bottom_material']))

        # Grip lip: the bottom face grows DOWN (its z0 drops), the top one grows UP.
        # Either way the box above stays where it was placed against the carcass.
        f_lip_b = lip_b if i == 0 else 0.0
        f_lip_t = lip_t if i == n - 1 else 0.0
        f_z0 = fz0 - f_lip_b
        f_h_c = face_h_c + f_lip_b + f_lip_t
        face_data = make_panel_data('Porta', label + ' Frente', f_h_c * 10.0, face_w_mm,
                                    drawer['face_material'], girar='Nao', band=ctx.fita_front)
        f_notes = 'corredica {0}, par {1:.0f}mm'.format(
            spec['description'], spec['nominal_length_mm'])
        f_lip_note = grip_note(f_lip_b, f_lip_t)
        if f_lip_note:
            f_notes += ', ' + f_lip_note
        face_data['complemento'] = '{0} Frente ({1})'.format(label, f_notes)
        add_solid_body(dcomp, label + ' Frente',
            (face_x0_c, face_y0_c, f_z0, face_w_c, face_t_c, f_h_c), face_data)

        for side, box in slide_proxy_slots(spec, box_x0_c, box_outer_w_c, box_y0_c,
                                           bz0, box_h_c, side_space_c, box_depth_c):
            nm = '{0} Corredica {1}'.format(label, side)
            if ctx.hw_comp is not None:
                ctx.hw_slots.append((nm, (box[0], box[1], box[2])))
            elif min(box[3], box[4], box[5]) > 1e-6:
                # A zero clearance (a runner with no gap of its own) leaves no room
                # for a proxy box — the drawer is still built, just unmarked.
                add_solid_body(dcomp, nm, box)

        ctx.drawer_bundles.append({'occ': drawer_occ, 'edge_x_c': box_x0_c,
                                   'edge_z_c': bz0, 'travel_c': box_depth_c * 0.9})


def build_blind(band, node, ctx, prefix):
    """A fixed 'blind' panel covering the region front — a solid piece of material
    that simply blocks access to the region (e.g. the dead corner of an L-shaped
    run). Laid out like a single overlay/inset front, but FIXED: it joins the rigid
    carcass (no hinge, no joint) and is grain-locked + edge-banded like any visible
    front. Overlay (default) applies it to the carcass front; inset seats it flush
    in the clear opening."""
    gap = ctx.door_gap if node.get('gap') is None else node['gap']
    gap_c = gap / 10.0
    inset = node.get('inset', False)
    pt = ctx.door_t
    pt_c = pt / 10.0
    if inset:
        span0_c = band.x0
        region_w_mm = (band.x1 - band.x0) * 10.0
        z0 = band.z0 + gap_c
        region_h_mm = (band.z1 - band.z0) * 10.0
        y0_c = 0.0
    else:
        span0_c = band.x0 - band.ext_l
        region_w_mm = ((band.x1 + band.ext_r) - (band.x0 - band.ext_l)) * 10.0
        z0 = (band.z0 - band.ext_b) + gap_c
        region_h_mm = ((band.z1 + band.ext_t) - (band.z0 - band.ext_b)) * 10.0
        y0_c = -pt_c
    w_mm = region_w_mm - 2 * gap
    h_mm = region_h_mm - 2 * gap
    ctx.blind_i += 1
    name = _pname(ctx, prefix, 'Cego', ctx.blind_i, single_ok=ctx.single_leaf)
    data = make_panel_data('Cego', name, h_mm, w_mm, ctx.door_material,
                           girar='Nao', band=ctx.fita_front)
    ctx.add_panel(name, (span0_c + gap_c, y0_c, z0, w_mm / 10.0, pt_c, h_mm / 10.0), data)


def build_region_leaf(band, node, ctx, prefix):
    typ = node.get('type', 'open')
    if typ == 'shelves':
        build_shelves(band, node, ctx, prefix)
    elif typ == 'doors':
        build_doors(band, node, ctx, prefix)
    elif typ == 'drawers':
        build_drawers(band, node, ctx, prefix)
    elif typ == 'blind':
        build_blind(band, node, ctx, prefix)
    # 'open' -> nothing


# Cabinet dimensions mirrored to Fusion User Parameters. Lengths (mm) go in the
# first list, plain counts (unitless) in the second. Curated on purpose — not the
# whole cfg.
_USER_PARAM_MM_FIELDS = ('W', 'H', 'D', 't', 'back_t', 'dado_depth', 'back_setback',
                         'toe_kick_t', 'toe_kick_height', 'toe_kick_setback',
                         'door_t', 'door_gap', 'drawer_gap')
_USER_PARAM_COUNT_FIELDS = ('n_shelves', 'n_doors', 'n_drawers')
_USER_PARAM_COMMENT = ('FusionMob managed (informacional). Edite via FusionMob > '
                       'Editar Armario; alterar este valor aqui nao tem efeito.')


def _next_param_prefix(design):
    """Lowest 'c<N>' prefix not already used by a published parameter set, so a new
    cabinet never reuses a living cabinet's parameter names (occurrence count would
    collide after a deletion). Falls back to 'c1' if userParameters is unavailable."""
    try:
        params = design.userParameters
    except Exception:
        return 'c1'
    n = 1
    while n < 100000:
        if not params.itemByName('fmob_c{0}_W'.format(n)):
            return 'c{0}'.format(n)
        n += 1
    return 'c{0}'.format(n)


def publish_user_parameters(design, cfg):
    """Mirror this cabinet's key dimensions to named Fusion User Parameters, so the
    model reads as parametric in the Parameters table. Names are prefixed per
    cabinet (fmob_<param_prefix>_<field>) to avoid collisions between cabinets in
    one document. Values are INFORMATIONAL: the geometry is static base features,
    so editing them here drives nothing (the stamped comment says so). Never
    raises — a parameter that can't be created/updated is just skipped."""
    try:
        params = design.userParameters
    except Exception:
        return
    prefix = cfg.get('param_prefix') or 'c1'

    def _set(name, expr, units):
        try:
            p = params.itemByName(name)
            if p:
                p.expression = expr
                try:
                    p.comment = _USER_PARAM_COMMENT
                except Exception:
                    pass
            else:
                params.add(name, adsk.core.ValueInput.createByString(expr),
                           units, _USER_PARAM_COMMENT)
        except Exception:
            pass

    for f in _USER_PARAM_MM_FIELDS:
        try:
            v = float(cfg.get(f))
        except (TypeError, ValueError):
            continue
        _set('fmob_{0}_{1}'.format(prefix, f), '{0} mm'.format(v), 'mm')
    for f in _USER_PARAM_COUNT_FIELDS:
        try:
            v = int(cfg.get(f))
        except (TypeError, ValueError):
            continue
        _set('fmob_{0}_{1}'.format(prefix, f), str(v), '')


def _iter_cabinet_bodies(occ):
    """Yield every BRepBody under a cabinet occurrence — its own component plus all
    nested child components (carcass panels, toe kick, doors, drawer bodies).
    Guarded so a malformed tree can't raise."""
    stack = [occ]
    while stack:
        o = stack.pop()
        try:
            comp = o.component
        except Exception:
            continue
        try:
            for b in comp.bRepBodies:
                yield b
        except Exception:
            pass
        try:
            for child in o.childOccurrences:
                stack.append(child)
        except Exception:
            pass


def capture_cabinet_state(occ):
    """Snapshot the state a delete-and-rebuild would otherwise lose: user-applied
    body appearance overrides, keyed by body name (the stable panel slot key).
    `body.appearance` reads None when the body just inherits, so only explicit
    overrides are captured. Returns {'appearances': {name: appearance}}. Never
    raises."""
    appearances = {}
    try:
        for body in _iter_cabinet_bodies(occ):
            try:
                ap = body.appearance
            except Exception:
                ap = None
            if ap is not None and body.name:
                appearances[body.name] = ap
    except Exception:
        pass
    return {'appearances': appearances}


def restore_cabinet_state(occ, state):
    """Re-apply a capture_cabinet_state snapshot onto a rebuilt cabinet, matching
    bodies by name. Bodies whose slot no longer exists (topology changed) are
    simply skipped. Never raises."""
    if not occ or not state:
        return
    appearances = state.get('appearances') or {}
    if not appearances:
        return
    try:
        for body in _iter_cabinet_bodies(occ):
            ap = appearances.get(body.name)
            if ap is not None:
                try:
                    body.appearance = ap
                except Exception:
                    pass
    except Exception:
        pass


def _carry_forward_cabinet_cfg(new_cfg, old_cfg):
    """Copy state the edit dialogs/palette don't round-trip — the published
    parameter prefix and the per-panel fita overrides — from a cabinet's previous
    cfg into the edited cfg, so a rebuild keeps its parameter names and Edit Panel
    tape choices. Incoming non-empty values win. Returns new_cfg."""
    if not isinstance(old_cfg, dict):
        return new_cfg
    if not new_cfg.get('param_prefix') and old_cfg.get('param_prefix'):
        new_cfg['param_prefix'] = old_cfg['param_prefix']
    if not new_cfg.get('panel_overrides') and isinstance(old_cfg.get('panel_overrides'), dict):
        new_cfg['panel_overrides'] = dict(old_cfg['panel_overrides'])
    return new_cfg


def build_cabinet(design, cfg, translation=None):
    """Build the carcass as one assembly of per-panel components from a config
    dict (all lengths in mm). Stores the config on the cabinet component so it
    can be edited later. `translation` (cm tuple) pins the position on rebuild.

    Returns (part_count, assembly_status, warnings) where warnings is a list of
    non-fatal notes (e.g. a hinge that couldn't be moved clear of a shelf)."""
    cfg = normalize_cfg(cfg)   # fill defaults + synthesize/normalize the layout
    # Per-panel fita overrides re-applied inside add_solid_panel/add_solid_body as
    # each tagged body is created (see _apply_panel_override).
    global _ACTIVE_PANEL_OVERRIDES
    _ACTIVE_PANEL_OVERRIDES = cfg.get('panel_overrides') or {}
    W, H, D, t = cfg['W'], cfg['H'], cfg['D'], cfg['t']
    n_shelves, material = cfg['n_shelves'], cfg['material']
    shelf_align_front = cfg.get('shelf_align_front', False)
    with_back, back_material = cfg['with_back'], cfg['back_material']
    back_mode = cfg.get('back_mode', 'groove')
    back_overlay = with_back and back_mode == 'overlay'
    back_t, dado_depth, back_setback = cfg['back_t'], cfg['dado_depth'], cfg['back_setback']
    with_toe_kick = cfg['with_toe_kick']
    toe_kick_material, toe_kick_t = cfg['toe_kick_material'], cfg['toe_kick_t']
    toe_kick_height, toe_kick_setback = cfg['toe_kick_height'], cfg['toe_kick_setback']
    toe_kick_max_span = cfg['toe_kick_max_span']
    with_doors, door_material = cfg['with_doors'], cfg['door_material']
    door_t, n_doors = cfg['door_t'], cfg['n_doors']
    door_gap = cfg['door_gap']
    door_inset = cfg['door_inset']
    with_hinges = with_doors and cfg['with_hinges']
    hinge = cfg.get('hinge', HINGE)
    tol = cfg['tol']
    joinery = cfg.get('joinery', JOINERY)
    # Tamponamento (acabamento) applied finish panels; material '' inherits carcass.
    tamp = cfg.get('tamponamento', TAMPONAMENTO)
    tamp_material = tamp.get('material') or material
    # Arremate (ajuste) scribe / gap-filler pieces; material '' inherits carcass.
    arremate = cfg.get('arremate', ARREMATE)
    arremate_material = arremate.get('material') or material
    # Puxador integrado (frente estendida): the handleless grip lip. The effective
    # length is resolved once here (clamped to the toe kick, see resolve_grip_size)
    # and read by the front builders through ctx.
    puxador = cfg.get('puxador', PUXADOR)
    grip_size = resolve_grip_size(cfg)
    # Edge banding tapes for this build (see FITA / fita_tape). '' when a group
    # is off. 'carcass' bands the front edge of sides/base/top/shelves/dividers;
    # 'fronts' bands doors/faces (all four) and the toe-kick front board.
    fita_cfg = cfg['fita']
    fita_carcass = fita_tape(fita_cfg, 'carcass')
    fita_front = fita_tape(fita_cfg, 'fronts')

    # The carcass rests on a separate toe-kick base, so the box height is the
    # overall height minus the kick, and every carcass panel is lifted by the
    # kick height. (Total Altura stays H; the base fills the bottom kick_h.)
    kick_h = toe_kick_height if with_toe_kick else 0.0
    Hbox = H - kick_h

    # Geometry works in internal units (cm).
    Wc, Hc, Dc, tc = W / 10.0, H / 10.0, D / 10.0, t / 10.0
    Hbox_c = Hbox / 10.0
    z_off = kick_h / 10.0

    # If the drawer slides will use the real CAD model, import it ONCE up front —
    # before the cabinet exists, so the import can't invalidate in-progress
    # geometry — then instance it at each slide position at the end of the build.
    hw_comp = None
    hw_parked_occ = None
    hw_xform = None
    hw_slots = []
    if cfg.get('with_drawers') and cfg.get('insert_real_hardware'):
        _spec0 = resolve_slide_spec(cfg)
        _mp = (os.path.join(HARDWARE_DIR, _spec0['model_file'])
               if _spec0.get('model_file') else '')
        if _mp and os.path.isfile(_mp):
            hw_parked_occ = import_hardware_component(design, _mp, 'Corredica (modelo)')
            if hw_parked_occ:
                hw_comp = hw_parked_occ.component
                hw_xform = _spec0.get('model_transform')

    root = design.rootComponent
    if translation is None:
        idx = root.occurrences.count
        translation = ((Wc + 10.0) * idx, 0.0, 0.0)
    # Stable per-cabinet prefix for the published user parameters, assigned once
    # and carried in cfg so rebuilds update the same params (see the edit handlers,
    # which carry it forward from the old cfg).
    if not cfg.get('param_prefix'):
        cfg['param_prefix'] = _next_param_prefix(design)
    # Timeline group start (parametric designs only) so the whole build collapses
    # into one named group. None disables grouping (direct-modeling docs).
    tl_start = None
    try:
        if design.designType == adsk.fusion.DesignTypes.ParametricDesignType:
            tl_start = design.timeline.count
    except Exception:
        tl_start = None
    cab_transform = adsk.core.Matrix3D.create()
    cab_transform.translation = adsk.core.Vector3D.create(
        translation[0], translation[1], translation[2])
    cabinet_occ = _add_new_component(root, cab_transform)
    cabinet_comp = cabinet_occ.component
    cabinet_comp.name = 'Cabinet {0}x{1}x{2}'.format(int(W), int(H), int(D))
    cabinet_comp.attributes.add(ATTR_GROUP, CABINET_CFG_ATTR, json.dumps(cfg))
    publish_user_parameters(design, cfg)

    inner_w = W - 2 * t  # clear width between the sides (mm)
    warnings = []

    # Handleless grip lip: report what the clamp did, and flag a bottom lip on a
    # cabinet with no toe kick (it then hangs below the base — fine for a wall
    # unit, surprising for one standing on the floor).
    if puxador.get('enabled') and puxador.get('side', 'bottom') != 'top':
        _asked = max(0.0, float(puxador.get('size', 0.0) or 0.0))
        if with_toe_kick and _asked > grip_size + 1e-6:
            warnings.append('Puxador: a aba inferior foi limitada a altura do rodape '
                            '({0:.0f}mm) para nao passar do piso.'.format(grip_size))
        elif not with_toe_kick and grip_size > 0:
            warnings.append('Puxador: sem rodape, a aba inferior fica abaixo da base do '
                            'armario (ok para armario aereo).')

    # --- Side<->base/top joinery (Fixacao Lateral) --------------------------
    # Per junction: 'aligned' captures the horizontal panel BETWEEN full-height
    # sides (today's geometry); 'over' runs the base/top the FULL width with the
    # side sitting on/under it. The interior datums (base top face z_off+tc, top
    # bottom face z_off+Hbox_c-tc) NEVER move, so only the side Z-extent and the
    # base/top width change here -- the region grid, back panel and hardware are
    # untouched. Overhang (mm) is how far the side reaches past the panel's inner
    # face toward its outer face: 0 = side rests on the base top / under the top;
    # == t = side flush with the base bottom / top top; > t = side skirts beyond
    # the panel (clamped to the floor at the bottom).
    bottom_over = joinery.get('bottom_mode') == 'over'
    top_over = joinery.get('top_mode') == 'over'
    # Sides-to-floor: the side legs run down to Z=0 through the toe-kick zone.
    # Only meaningful when there IS a kick to span (else the side already sits on
    # the floor at z_off=0).
    sides_to_floor = bool(joinery.get('sides_to_floor', False)) and with_toe_kick and kick_h > 0
    side_z0 = z_off
    if bottom_over:
        side_z0 = z_off + tc - max(0.0, float(joinery.get('bottom_overhang', 0.0))) / 10.0
        if side_z0 < 0.0:
            side_z0 = 0.0
            warnings.append('Fixacao lateral (base): avanco maior que o espaco '
                            'disponivel; a lateral foi limitada ao piso.')
    if sides_to_floor:
        side_z0 = 0.0   # legs reach the floor; base top face (z_off+tc) is unchanged
    side_z1 = z_off + Hbox_c
    if top_over:
        side_z1 = z_off + Hbox_c - tc + max(0.0, float(joinery.get('top_overhang', 0.0))) / 10.0
    side_len_mm = (side_z1 - side_z0) * 10.0
    # Base/top X placement: full width in 'over', else captured between the sides.
    base_x0_c = 0.0 if bottom_over else tc
    base_w_c = Wc if bottom_over else (Wc - 2 * tc)
    base_dim_a = W if bottom_over else inner_w
    top_x0_c = 0.0 if top_over else tc
    top_w_c = Wc if top_over else (Wc - 2 * tc)
    top_dim_a = W if top_over else inner_w
    # Where a full-width panel and the side skirt would share volume (the side
    # descends past the base top / rises past the top bottom by the overhang), cut
    # a matching end-rebate (rebaixo) into the panel so the parts stay flush with
    # NO interference. CorteCloud has no usinagem field, so the rebate depth is
    # noted in Complemento (same convention as hinge/slide furacao). Each notch is
    # an (x0,y0,z0,dx,dy,dz) cm box at the two ends, spanning the full depth.
    base_notches = []
    base_note = 'Base'
    if bottom_over:
        nz0 = max(side_z0, z_off)
        ndz = (z_off + tc) - nz0
        if ndz > 1e-4:
            base_notches = [(0.0, 0.0, nz0, tc, Dc, ndz), (Wc - tc, 0.0, nz0, tc, Dc, ndz)]
            base_note = 'Base (rebaixo lateral {0:.0f}mm nas pontas)'.format(ndz * 10.0)
    top_notches = []
    top_note = 'Tampo'
    if top_over:
        ntz1 = min(side_z1, z_off + Hbox_c)
        ntz0 = z_off + Hbox_c - tc
        ndzt = ntz1 - ntz0
        if ndzt > 1e-4:
            top_notches = [(0.0, 0.0, ntz0, tc, Dc, ndzt), (Wc - tc, 0.0, ntz0, tc, Dc, ndzt)]
            top_note = 'Tampo (rebaixo lateral {0:.0f}mm nas pontas)'.format(ndzt * 10.0)

    # Organise the model into sub-assemblies under the cabinet: the carcass box
    # (Corpo), the toe kick (Rodape), and one component per drawer (Gaveta N).
    # Doors stay directly under the cabinet. Every panel is still built at the
    # same cabinet-local coordinates; the sub-components carry identity
    # transforms, so nothing moves — this is purely organisational.
    carcass_occ = new_part_component(cabinet_comp, 'Corpo')
    carcass_comp = carcass_occ.component
    carcass_occs = []

    def add_panel(name, box, data, grooves=None, holes=None):
        occ = add_solid_panel(carcass_comp, name, box, data, grooves, holes)
        carcass_occs.append(occ)
        return occ

    # Precompute the dado grooves (cm) when there's a GROOVE-mounted back panel.
    # Each groove is cut the full 'dd' deep and 'back_t + 2*sc' wide so the back
    # seats with bottom + side clearance and never fills the whole slot. An
    # overlay (sobreposto) back is applied to the rear face instead -- no grooves.
    left_g = right_g = base_g = top_g = None
    if with_back and not back_overlay:
        dd = dado_depth                          # groove depth into each panel (mm)
        sc = tol['dado_side_clearance']          # play on the back's faces in the groove
        by0 = D - back_setback - back_t          # back front face (mm from front)
        ddc = dd / 10.0
        gw_c = (back_t + 2 * sc) / 10.0          # groove width along Y
        gy0_c = (by0 - sc) / 10.0                # groove near face along Y
        left_g = [(tc - ddc, gy0_c, z_off, ddc, gw_c, Hbox_c)]
        right_g = [(Wc - tc, gy0_c, z_off, ddc, gw_c, Hbox_c)]
        base_g = [(tc, gy0_c, z_off + tc - ddc, Wc - 2 * tc, gw_c, ddc)]
        top_g = [(tc, gy0_c, z_off + Hbox_c - tc, Wc - 2 * tc, gw_c, ddc)]

    # Where the back panel's front face sits (mm from the front). Shelves, drawer
    # boxes and interior dividers all stop at (or just short of) this plane. A
    # GROOVE back recesses the interior by (setback + thickness); an OVERLAY back
    # is applied flush to the rear edges (y = D) and extends behind, so the
    # interior runs the full depth (like having no back).
    back_front_y = (D - back_setback - back_t) if (with_back and not back_overlay) else D

    # Base and top: captured between the sides ('aligned') or running the full
    # width ('over'); see the joinery block above. Thickness runs along Z; their
    # inner/outer faces (base top, top bottom) are the invariant interior datums.
    # The SIDES are created later (after the interior walk) because door hinge
    # plates bore into them and those hole positions are only known once the
    # regions are laid out.
    base_grooves = (base_g or []) + base_notches
    top_grooves = (top_g or []) + top_notches
    add_panel('Base', (base_x0_c, 0.0, z_off, base_w_c, Dc, tc),
              make_panel_data('Base', base_note, base_dim_a, D, material,
                              bands={'a': (fita_carcass, '')}), base_grooves or None)
    add_panel('Tampo', (top_x0_c, 0.0, z_off + Hbox_c - tc, top_w_c, Dc, tc),
              make_panel_data('Tampo', top_note, top_dim_a, D, material,
                              bands={'a': (fita_carcass, '')}), top_grooves or None)

    # Back panel. GROOVE mode: reaches 'engage' (= dd - bottom clearance) into all
    # four grooves, sized inner_w/(Hbox-2t) plus that engagement. OVERLAY mode:
    # a full-width x full-carcass-height board applied (screwed) to the rear face
    # of the carcass -- its front face flush with the sides/base/top rear edges
    # (y = D) and extending back_t behind, so it never shares volume with the
    # carcass (no grooves, no interference) and the interior runs full depth.
    if with_back and back_overlay:
        back_w = W
        back_h = Hbox
        add_panel('Fundo',
                  (0.0, Dc, z_off,
                   back_w / 10.0, back_t / 10.0, back_h / 10.0),
                  make_panel_data('Fundo', 'Fundo', back_w, back_h, back_material))
    elif with_back:
        engage = dd - tol['dado_bottom_clearance']
        engc = engage / 10.0
        by0_c = (D - back_setback - back_t) / 10.0
        back_w = inner_w + 2 * engage
        back_h = (Hbox - 2 * t) + 2 * engage
        add_panel('Fundo',
                  (tc - engc, by0_c, z_off + tc - engc,
                   back_w / 10.0, back_t / 10.0, back_h / 10.0),
                  make_panel_data('Fundo', 'Fundo', back_w, back_h, back_material))

    # Toe-kick base (rodapé): a self-contained box below the carcass, built from
    # rails so it never relies on the cabinet's side panels. A front board
    # (recessed for foot clearance) and a back rail span the full width; end
    # connectors and evenly spaced reinforcements (reforços) run front-to-back so
    # no unsupported bay exceeds toe_kick_max_span.
    kick_occ = None
    kick_occs = []
    if with_toe_kick:
        kh_c = toe_kick_height / 10.0
        kt_c = toe_kick_t / 10.0
        s_c = toe_kick_setback / 10.0

        conn_y0 = s_c + kt_c          # connectors start behind the front board
        conn_y1 = Dc - kt_c           # ...and butt into the back rail
        conn_len_c = conn_y1 - conn_y0
        conn_len_mm = conn_len_c * 10.0

        # The toe kick is its own sub-assembly (Rodape) under the cabinet.
        kick_occ = new_part_component(cabinet_comp, 'Rodape')
        kick_comp = kick_occ.component

        def add_kick(name, box, data):
            kick_occs.append(add_solid_panel(kick_comp, name, box, data))

        # Front (visible) board + back rail. With sides-to-floor the cabinet
        # sides ARE the legs, so the boards span BETWEEN them (recessed) and there
        # are no end connectors; otherwise both span the full width.
        board_x0 = tc if sides_to_floor else 0.0
        board_w_c = (Wc - 2 * tc) if sides_to_floor else Wc
        board_w_mm = inner_w if sides_to_floor else W
        add_kick('Rodape Frente', (board_x0, s_c, 0.0, board_w_c, kt_c, kh_c),
                 make_panel_data('Rodape', 'Rodape Frente', board_w_mm, toe_kick_height, toe_kick_material,
                                 bands={'a': (fita_front, '')}))
        add_kick('Rodape Traseira', (board_x0, conn_y1, 0.0, board_w_c, kt_c, kh_c),
                 make_panel_data('Travessa', 'Rodape Traseira', board_w_mm, toe_kick_height, toe_kick_material))

        def add_kick_conn(name, x0):
            add_kick(name, (x0, conn_y0, 0.0, kt_c, conn_len_c, kh_c),
                     make_panel_data('Travessa', name, conn_len_mm, toe_kick_height, toe_kick_material))

        # End connectors only when the kick is a standalone box (sides stop at the
        # base); with sides-to-floor the cabinet sides already close the ends.
        # Then interior reinforcements divide the clear span into equal bays no
        # wider than toe_kick_max_span.
        if sides_to_floor:
            rx0, rx1, clear_w = tc, Wc - tc, inner_w          # between the side legs
        else:
            add_kick_conn('Rodape Lateral E', 0.0)
            add_kick_conn('Rodape Lateral D', Wc - kt_c)
            rx0, rx1, clear_w = kt_c, Wc - kt_c, W - 2 * toe_kick_t   # between end connectors
        n_bays = max(1, int(math.ceil(clear_w / toe_kick_max_span)))
        span_c = rx1 - rx0
        for j in range(1, n_bays):
            cx = rx0 + j * (span_c / n_bays) - kt_c / 2.0
            add_kick_conn('Rodape Reforco {0}'.format(j), cx)

    # ---- Interior: recursive region grid ----------------------------------
    # The interior is divided into a recursive grid of regions (cfg['layout']);
    # each leaf renders open / shelves / doors / drawers inside its own band, and
    # a divider panel of carcass thickness separates split children. The builders
    # are band-aware ports of the old full-carcass code, so a single-region
    # cabinet reproduces the classic geometry exactly. Doors and drawers are NOT
    # part of the rigid carcass — each is collected so it can be given its own
    # pivot / slider joint after assembly.
    ctx = _BuildCtx()
    ctx.design = design
    ctx.cabinet_comp = cabinet_comp
    ctx.carcass_comp = carcass_comp
    ctx.add_panel = add_panel
    ctx.material = material
    ctx.door_material = door_material
    ctx.fita_carcass = fita_carcass
    ctx.fita_front = fita_front
    ctx.door_gap = door_gap
    ctx.door_t = door_t
    ctx.drawer_gap = cfg['drawer_gap']
    ctx.drawer = cfg['drawer']
    ctx.slide_key = cfg['slide_key']
    ctx.slide = cfg['slide']            # per-cabinet override of the slide spec
    ctx.t = t
    ctx.tc = tc
    ctx.D = D
    ctx.Dc = Dc
    ctx.with_back = with_back
    ctx.back_front_y = back_front_y
    ctx.tol = tol
    ctx.hinge = hinge
    ctx.with_hinges = cfg['with_hinges']    # per-door boring is gated inside build_doors
    ctx.hw_comp = hw_comp
    ctx.hw_xform = hw_xform
    ctx.hw_slots = hw_slots
    ctx.shelf_align_front_default = shelf_align_front
    # Handleless grip lip (puxador integrado): the overlay fronts that reach the
    # carcass bottom/top face are cut longer so their protruding edge is the grip.
    # Nothing else moves — the builders read these three through grip_lip_extents.
    ctx.puxador = puxador
    ctx.grip_size = grip_size
    ctx.carcass_z0_c = z_off              # carcass bottom face (base underside)
    ctx.carcass_z1_c = z_off + Hbox_c     # carcass top face
    ctx.warnings = warnings
    ctx.door_occs = []
    ctx.drawer_bundles = []
    ctx.hole_map = {}
    ctx.door_i = ctx.drawer_i = ctx.shelf_i = ctx.blind_i = 0
    layout = cfg['layout']
    ctx.single_leaf = not is_split(layout)

    root_band = _Band(tc, Wc - tc, z_off + tc, z_off + Hbox_c - tc,
                      tc, tc, tc, tc, 'L', 'R')
    div_depth_c = back_front_y / 10.0
    leaves, dividers = plan_layout(root_band, layout, tc, div_depth_c)

    for band, node, prefix in leaves:
        build_region_leaf(band, node, ctx, prefix)

    # Interior dividers, created now that hole_map holds any door plate holes.
    # Vertical ('v') dividers are mountable and may carry those holes; horizontal
    # ('h') dividers do not. Both are tagged so they reach the cut list.
    for d in dividers:
        holes = ctx.hole_map.get(d['key']) if d['key'] is not None else None
        a_mm, b_mm = d['data_dims']
        add_panel(d['name'], d['box'],
                  make_panel_data(d['funcao'], d['name'], a_mm, b_mm, material,
                                  bands={'a': (fita_carcass, '')}),
                  None, holes)

    # Sides LAST: from side_z0 to side_z1 (set by the joinery mode) x depth,
    # thickness along X, with the back grooves and any accumulated hinge plate
    # holes. (anchor stays the Corpo occ.) In 'aligned' mode this is the full box
    # height (side_z0=z_off, side_z1=z_off+Hbox_c) exactly as before.
    side_dz_c = side_z1 - side_z0
    add_panel('Lateral Esquerda', (0.0, 0.0, side_z0, tc, Dc, side_dz_c),
              make_panel_data('Lateral', 'Lateral Esquerda', side_len_mm, D, material,
                              bands={'a': (fita_carcass, '')}),
              left_g, ctx.hole_map.get('L'))
    add_panel('Lateral Direita', (Wc - tc, 0.0, side_z0, tc, Dc, side_dz_c),
              make_panel_data('Lateral', 'Lateral Direita', side_len_mm, D, material,
                              bands={'a': (fita_carcass, '')}),
              right_g, ctx.hole_map.get('R'))

    # Tamponamento (applied finish panels over exposed faces). Each sits OUTSIDE
    # the structural face, so it extends past the nominal W x H envelope. Sides
    # run the full height (0..Hc, incl. the toe-kick recess); the top caps the
    # full finished width (covering the side tamponamentos when present). Depth:
    # the rear edge stays flush with the cabinet back (y = Dc) and the panels can
    # project FORWARD past the carcass front by 'front_overhang' (y0 = -proj_c),
    # so their depth = D + front_overhang. The interior is untouched (like the
    # overlay back panel). Front edge banded with the fronts tape, grain locked
    # (Girar=Nao) as a visible finish. These join the carcass rigid group / cut
    # list via add_panel like any other panel.
    Hc = z_off + Hbox_c   # carcass top face (== H/10)
    tt_c = tamp['t'] / 10.0
    tamp_proj_c = float(tamp.get('front_overhang', 0.0)) / 10.0
    tamp_y0 = -tamp_proj_c                 # front edge (projects forward when > 0)
    tamp_dy = Dc + tamp_proj_c             # full depth incl. the forward projection
    tamp_depth_mm = D + float(tamp.get('front_overhang', 0.0))   # cut-list depth
    if tamp.get('left'):
        add_panel('Tamponamento Esquerdo', (-tt_c, tamp_y0, 0.0, tt_c, tamp_dy, Hc),
                  make_panel_data('Tamponamento', 'Tamponamento Esquerdo', H, tamp_depth_mm,
                                  tamp_material, girar='Nao',
                                  bands={'a': (fita_front, '')}))
    if tamp.get('right'):
        add_panel('Tamponamento Direito', (Wc, tamp_y0, 0.0, tt_c, tamp_dy, Hc),
                  make_panel_data('Tamponamento', 'Tamponamento Direito', H, tamp_depth_mm,
                                  tamp_material, girar='Nao',
                                  bands={'a': (fita_front, '')}))
    if tamp.get('top'):
        top_x0 = -tt_c if tamp.get('left') else 0.0
        top_w_c = Wc + (tt_c if tamp.get('left') else 0.0) + (tt_c if tamp.get('right') else 0.0)
        add_panel('Tamponamento Superior', (top_x0, tamp_y0, Hc, top_w_c, tamp_dy, tt_c),
                  make_panel_data('Tamponamento', 'Tamponamento Superior', top_w_c * 10.0,
                                  tamp_depth_mm, tamp_material, girar='Nao',
                                  bands={'a': (fita_front, '')}))

    # Arremate (ajuste): scribe / gap-filler FRONT pieces. Thin boards (thickness
    # in Y = arr_t_c), front face flush with the carcass front (y in [0, arr_t_c]),
    # sitting OUTSIDE the box so the interior is untouched (like the overlay back /
    # tamponamento). Grain locked (Girar=Nao), banded with the fronts tape, and
    # each carries an "ajustar no local" note -> cut oversized and trimmed on site.
    #   * Side reguas run the full finished height (floor -> ceiling when a top
    #     sanefa is present, else floor -> carcass top) and stand beside the box.
    #   * The top sanefa spans only the carcass width (x in [0, Wc]) between the
    #     reguas, so the pieces never overlap. Envelope grows up by top_gap and out
    #     by side_gap per enabled side.
    arr_t_c = float(arremate.get('t', 18.0)) / 10.0
    arr_top = bool(arremate.get('top'))
    # Effective gap: entered directly ('gap' mode) or derived from the room's total
    # ceiling height ('ceiling' mode = ceiling_height - H). Single source of truth.
    arr_top_gap_mm = resolve_arremate_top_gap(cfg)
    arr_top_gap_c = arr_top_gap_mm / 10.0
    arr_side_gap_c = float(arremate.get('side_gap', 0.0)) / 10.0
    arr_y0, arr_dy = 0.0, arr_t_c            # front face flush at y = 0
    # Full finished height: reach the ceiling when a top sanefa closes that gap.
    arr_side_h_c = Hc + (arr_top_gap_c if arr_top else 0.0)
    arr_side_h_mm = arr_side_h_c * 10.0
    if arremate.get('left'):
        add_panel('Arremate Esquerdo',
                  (-arr_side_gap_c, arr_y0, 0.0, arr_side_gap_c, arr_dy, arr_side_h_c),
                  make_panel_data('Arremate', 'Arremate Esquerdo (ajustar no local)',
                                  arr_side_h_mm, arremate['side_gap'], arremate_material,
                                  girar='Nao', bands={'a': (fita_front, '')}))
    if arremate.get('right'):
        add_panel('Arremate Direito',
                  (Wc, arr_y0, 0.0, arr_side_gap_c, arr_dy, arr_side_h_c),
                  make_panel_data('Arremate', 'Arremate Direito (ajustar no local)',
                                  arr_side_h_mm, arremate['side_gap'], arremate_material,
                                  girar='Nao', bands={'a': (fita_front, '')}))
    if arr_top:
        # Sanefa faceada com as frentes ('top_inline_fronts'): overlay fronts project
        # forward by their thickness (door front at y=-door_t, drawer face at
        # y=-face_t), leaving a flush sanefa recessed. When on, the whole sanefa
        # assembly is pushed forward so its visible face lands on the overlay front
        # plane (y = -front_reach); the structural board stays flush at y=0 and a
        # facing sheet fills the gap in front of it. Derived from the enabled
        # non-inset fronts; 0 (no-op) when there are none.
        front_reach_mm = 0.0
        if arremate.get('top_inline_fronts'):
            if with_doors and not door_inset:
                front_reach_mm = max(front_reach_mm, float(door_t))
            if cfg.get('with_drawers') and not cfg.get('drawer_inset'):
                front_reach_mm = max(front_reach_mm, float(cfg['drawer']['face_t']))
        fr_c = front_reach_mm / 10.0
        front_y0 = -fr_c                         # front face of the sanefa assembly
        # Sanefa em U ('top_side_returns', on by default): two side returns cap the
        # exposed ends so the valance looks finished from the laterals. Each return
        # is a full-depth board sitting directly above its carcass side (x in
        # [0,t] / [Wc-t,Wc]), running front-to-back from the sanefa front face to the
        # cabinet back (y in [front_y0, Dc]) at the gap height, i.e. the side visually
        # continues up to the ceiling. To avoid sharing volume with the returns, the
        # front board then spans only BETWEEN them (x in [t, Wc-t]); without the U it
        # spans the full carcass width. No overlap: front and returns butt at x=t /
        # x=Wc-t. The returns' visible (forward) edge is the top_gap-length edge -> 'b'.
        arr_us = bool(arremate.get('top_side_returns', True))
        front_x0 = arr_t_c if arr_us else 0.0
        front_x1 = (Wc - arr_t_c) if arr_us else Wc
        front_w_c = front_x1 - front_x0
        front_w_mm = front_w_c * 10.0
        # (1) structural front board, flush at the carcass front (y in [0, arr_t_c]).
        # Guarded for a degenerate cabinet narrower than the two returns (Wc <= 2t),
        # where the front board would vanish and the returns fill the whole width.
        if front_w_c > 0:
            add_panel('Arremate Superior',
                      (front_x0, 0.0, Hc, front_w_c, arr_t_c, arr_top_gap_c),
                      make_panel_data('Arremate', 'Arremate Superior (ajustar no local)',
                                      front_w_mm, arr_top_gap_mm, arremate_material,
                                      girar='Nao', bands={'a': (fita_front, '')}))
            # (2) facing sheet in front of it, filling the depth to the overlay front plane.
            if fr_c > 0:
                add_panel('Arremate Superior Frente',
                          (front_x0, front_y0, Hc, front_w_c, fr_c, arr_top_gap_c),
                          make_panel_data('Arremate',
                                          'Arremate Superior Frente (faceado, ajustar no local)',
                                          front_w_mm, arr_top_gap_mm, arremate_material,
                                          girar='Nao', bands={'a': (fita_front, '')}))
        # (3) side returns (U-shape) — full depth from the front face to the back.
        if arr_us:
            ret_dy = Dc - front_y0               # front_y0 <= 0 -> Dc + fr_c
            ret_depth_mm = ret_dy * 10.0
            add_panel('Arremate Superior Retorno Esquerdo',
                      (0.0, front_y0, Hc, arr_t_c, ret_dy, arr_top_gap_c),
                      make_panel_data('Arremate',
                                      'Arremate Superior Retorno Esquerdo (ajustar no local)',
                                      ret_depth_mm, arr_top_gap_mm, arremate_material,
                                      girar='Nao', bands={'b': (fita_front, '')}))
            add_panel('Arremate Superior Retorno Direito',
                      (Wc - arr_t_c, front_y0, Hc, arr_t_c, ret_dy, arr_top_gap_c),
                      make_panel_data('Arremate',
                                      'Arremate Superior Retorno Direito (ajustar no local)',
                                      ret_depth_mm, arr_top_gap_mm, arremate_material,
                                      girar='Nao', bands={'b': (fita_front, '')}))

    door_occs = ctx.door_occs
    drawer_bundles = ctx.drawer_bundles
    part_count = len(carcass_occs) + len(kick_occs) + len(door_occs) + len(drawer_bundles) * 6

    # Lock the carcass + toe kick into one static structure, ground it, then let
    # the doors pivot and the drawers slide relative to it. (Each drawer is one
    # component, so no internal grouping is needed.)
    _rigid_group_occs(carcass_comp, carcass_occs)
    if kick_occ:
        _rigid_group_occs(kick_comp, kick_occs)

    status = 'grouped'
    _rigid_group_occs(cabinet_comp, [carcass_occ, kick_occ])
    try:
        carcass_occ.isGrounded = True
    except Exception:
        pass

    if door_occs:
        attach_door_pivots(cabinet_comp, carcass_occ, door_occs)
    if drawer_bundles:
        attach_drawer_slides(cabinet_comp, carcass_occ, drawer_bundles)

    # Place the real slide model (imported once, up front) at each recorded slot
    # as an instance under the cabinet, then remove the temporary root-level
    # import. Instances are grounded so they stay put and are not part of the
    # moving drawer unit. Any that fail just leave that slot without a model.
    if hw_comp is not None and hw_slots:
        for nm, base_cm in hw_slots:
            try:
                inst = cabinet_comp.occurrences.addExistingComponent(
                    hw_comp, _matrix_from_transform(hw_xform, base_cm))
                try:
                    inst.isGrounded = True
                except Exception:
                    pass
            except Exception:
                pass
    if hw_parked_occ is not None:
        try:
            hw_parked_occ.deleteMe()
        except Exception:
            pass

    # Collapse this cabinet's timeline features into one named group so the build
    # reads as a single unit. Best-effort: a range that can't be grouped is left
    # ungrouped rather than failing the build.
    if tl_start is not None:
        try:
            tl_end = design.timeline.count - 1
            if tl_end >= tl_start:
                grp = design.timeline.timelineGroups.add(tl_start, tl_end)
                try:
                    grp.name = 'FusionMob ' + cabinet_comp.name
                except Exception:
                    pass
        except Exception:
            pass

    _ACTIVE_PANEL_OVERRIDES = {}
    return part_count, status, warnings


# Cabinets available to the active Edit Cabinet command, aligned with the
# 'cabinetPick' dropdown order. Only one edit command runs at a time.
_edit_cabinets = []


# -----------------------------------------------------------------------------
# Interior layout (region tree). A cabinet's interior is divided into a recursive
# grid of regions: a SPLIT node stacks/columns its children (with a divider panel
# of carcass thickness between each pair), and a LEAF node renders one of the four
# contents. 'v' splits stack rows top-to-bottom (horizontal dividers); 'h' splits
# place columns left-to-right (vertical dividers). Child sizes are absolute mm
# (fixed=True) or flex weights (fixed=False) that share the leftover space.
# -----------------------------------------------------------------------------
LEAF_TYPES = ('open', 'shelves', 'doors', 'drawers', 'blind')


def is_split(node):
    return isinstance(node, dict) and 'split' in node


def layout_has_overlay_fronts(node):
    """True when the region tree carries at least one OVERLAY (sobreposta) door or
    drawer leaf — i.e. a front the handleless grip lip could extend. Pure."""
    if is_split(node):
        return any(layout_has_overlay_fronts(ch.get('node'))
                   for ch in (node.get('children') or []))
    if not isinstance(node, dict):
        return False
    return node.get('type') in ('doors', 'drawers') and not node.get('inset')


def is_layout_split(cfg):
    """True when the cabinet carries a non-trivial (multi-region) layout the
    classic New/Edit dialog cannot represent — used to defer to the palette."""
    lay = cfg.get('layout')
    return is_split(lay)


def _synthesize_layout_from_flat(cfg):
    """Build a single top-level LEAF from the flat fields, reproducing today's
    exclusive interior so any pre-layout cabinet still opens and rebuilds the
    same. Drawers win over doors win over shelves (matching the old validation)."""
    if cfg.get('with_drawers'):
        return {'type': 'drawers', 'count': int(cfg.get('n_drawers', 1)),
                'inset': bool(cfg.get('drawer_inset', False)),
                'gap': cfg.get('drawer_gap'), 'slide_key': cfg.get('slide_key')}
    if cfg.get('with_doors') and int(cfg.get('n_shelves', 0)) > 0:
        # Today's "doors on the front + shelves inside" cabinet.
        return {'type': 'doors', 'count': int(cfg.get('n_doors', 1)),
                'inset': bool(cfg.get('door_inset', False)),
                'gap': cfg.get('door_gap'),
                'shelves_behind': int(cfg.get('n_shelves', 0))}
    if cfg.get('with_doors'):
        return {'type': 'doors', 'count': int(cfg.get('n_doors', 1)),
                'inset': bool(cfg.get('door_inset', False)), 'gap': cfg.get('door_gap')}
    if int(cfg.get('n_shelves', 0)) > 0:
        return {'type': 'shelves', 'count': int(cfg.get('n_shelves', 0)),
                'shelf_align_front': bool(cfg.get('shelf_align_front', False))}
    return {'type': 'open'}


def _normalize_layout_node(node):
    """Deep-fill a layout node's optional fields so the builder/validator can rely
    on them. Unknown/garbage shapes degrade to an open leaf."""
    if is_split(node):
        split = node.get('split')
        if split not in ('v', 'h'):
            split = 'v'
        raw_children = node.get('children') or []
        children = []
        for ch in raw_children:
            if not isinstance(ch, dict):
                continue
            children.append({
                'size': float(ch.get('size', 1.0) or 1.0),
                'fixed': bool(ch.get('fixed', False)),
                'node': _normalize_layout_node(ch.get('node')),
            })
        if not children:                      # a split with no children is just open
            return {'type': 'open'}
        return {'split': split, 'children': children}
    # Leaf.
    if not isinstance(node, dict):
        return {'type': 'open'}
    typ = node.get('type', 'open')
    if typ not in LEAF_TYPES:
        typ = 'open'
    out = {'type': typ}
    out['count'] = max(1, int(node.get('count', 1) or 1))
    out['inset'] = bool(node.get('inset', False))
    out['gap'] = node.get('gap')                       # None => inherit cfg
    out['shelf_align_front'] = node.get('shelf_align_front')
    out['slide_key'] = node.get('slide_key')
    out['shelves_behind'] = int(node.get('shelves_behind', 0) or 0)
    return out


def normalize_cfg(cfg):
    """Fill any missing keys from the defaults (robust to older stored configs)."""
    out = dict(DEFAULT_CFG)
    out.update({k: cfg[k] for k in cfg if k not in ('tol', 'hinge', 'drawer', 'slide', 'fita', 'joinery', 'tamponamento', 'arremate', 'puxador', 'layout')})
    tol = dict(DEFAULT_TOL)
    if isinstance(cfg.get('tol'), dict):
        tol.update(cfg['tol'])
    out['tol'] = tol
    hinge = dict(HINGE)
    if isinstance(cfg.get('hinge'), dict):
        hinge.update(cfg['hinge'])
    out['hinge'] = hinge
    drawer = dict(DRAWER)
    if isinstance(cfg.get('drawer'), dict):
        drawer.update(cfg['drawer'])
    out['drawer'] = drawer
    slide = dict(SLIDE)
    if isinstance(cfg.get('slide'), dict):
        slide.update(cfg['slide'])
    out['slide'] = slide
    fita = dict(FITA)
    if isinstance(cfg.get('fita'), dict):
        fita.update(cfg['fita'])
    out['fita'] = fita
    joinery = dict(JOINERY)
    if isinstance(cfg.get('joinery'), dict):
        joinery.update(cfg['joinery'])
    out['joinery'] = joinery
    tamp = dict(TAMPONAMENTO)
    if isinstance(cfg.get('tamponamento'), dict):
        tamp.update(cfg['tamponamento'])
    out['tamponamento'] = tamp
    arremate = dict(ARREMATE)
    if isinstance(cfg.get('arremate'), dict):
        arremate.update(cfg['arremate'])
    out['arremate'] = arremate
    puxador = dict(PUXADOR)
    if isinstance(cfg.get('puxador'), dict):
        puxador.update(cfg['puxador'])
    out['puxador'] = puxador
    # Per-panel fita overrides: always a fresh dict so we never alias the shared
    # DEFAULT_CFG default across cabinets.
    po = cfg.get('panel_overrides')
    out['panel_overrides'] = dict(po) if isinstance(po, dict) else {}
    # Layout: synthesize a single region from the flat fields when absent (old
    # configs / classic dialog); otherwise deep-fill the explicit tree.
    lay = cfg.get('layout')
    out['layout'] = _normalize_layout_node(lay if lay else _synthesize_layout_from_flat(out))
    return out


def _select_dropdown(dd, name):
    for it in dd.listItems:
        if it.name == name:
            it.isSelected = True
            return
    if dd.listItems.count:
        dd.listItems.item(0).isSelected = True


# Inputs hidden until "Configuracao avancada" is ticked, so the New/Edit Cabinet
# dialog opens clean — every default already comes from Preferences. Hiding is
# purely cosmetic: read_cabinet_inputs reads each input by id regardless of
# visibility, so the (default or loaded) values still drive the build.
_CABINET_ADVANCED_IDS = ('thickness', 'shelfAlignFront', 'backGroup', 'toeKickGroup',
                         'doorGroup', 'drawerGroup', 'joineryGroup', 'tamponamentoGroup',
                         'arremateGroup', 'puxadorGroup', 'fitaGroup', 'advGroup')


def _apply_cabinet_advanced_visibility(inputs, visible):
    """Show/hide the advanced (Preferences-defaulted) inputs as a group."""
    for cid in _CABINET_ADVANCED_IDS:
        item = inputs.itemById(cid)
        if item:
            item.isVisible = bool(visible)


def _apply_arremate_top_mode_visibility(inputs):
    """Show only the field that matches the chosen sanefa-measurement mode: the
    'Folga ate o teto' input for 'gap', the 'Altura do teto' input for 'ceiling'."""
    dd = inputs.itemById('arrTopMode')
    if not dd or not dd.selectedItem:
        return
    by_ceiling = _arremate_top_mode_value(dd.selectedItem.name) == 'ceiling'
    gap_in = inputs.itemById('arrTopGap')
    ceil_in = inputs.itemById('arrCeilingHeight')
    if gap_in:
        gap_in.isVisible = not by_ceiling
    if ceil_in:
        ceil_in.isVisible = by_ceiling


_SLIDE_UI_FIELDS = ('side_space', 'bottom_clearance', 'back_clearance',
                    'box_depth', 'min_cabinet_depth')

# Dialog input ids for the five editable slide numbers, in _SLIDE_UI_FIELDS order.
_SLIDE_INPUT_IDS = ('slideSideSpace', 'slideBottomClearance', 'slideBackClearance',
                    'slideBoxDepth', 'slideMinDepth')


def slide_ui_values(cfg):
    """The five editable slide numbers (mm) to show for this cfg: the stored
    overrides when 'personalizar' is on, otherwise a live readout of the chosen
    library slide's own values."""
    sl = cfg.get('slide') if isinstance(cfg.get('slide'), dict) else SLIDE
    if sl.get('custom'):
        out = {}
        for k in _SLIDE_UI_FIELDS:
            try:
                out[k] = float(sl.get(k, SLIDE[k]))
            except (TypeError, ValueError):
                out[k] = float(SLIDE[k])
        return out
    spec = resolve_slide_spec({'slide_key': cfg.get('slide_key')})
    drawer = cfg.get('drawer') if isinstance(cfg.get('drawer'), dict) else DRAWER
    return {
        'side_space': slide_side_space(spec, drawer.get('box_t', DRAWER['box_t'])),
        'bottom_clearance': spec.get('bottom_clearance', 0.0),
        'back_clearance': spec.get('back_clearance', 0.0),
        'box_depth': spec.get('recommended_box_depth', 0.0),
        'min_cabinet_depth': spec.get('min_cabinet_depth', 0.0),
    }


def _apply_slide_custom_state(inputs, refresh_from_spec=False):
    """Keep the five slide-measurement inputs in step with the 'personalizar'
    checkbox: read-only (a live readout of the selected slide) while it is off,
    editable and authoritative while it is on. `refresh_from_spec` re-reads the
    numbers from the chosen slide — done when the slide changes, but never while
    'personalizar' is on, so the user's own figures are not overwritten."""
    custom_in = inputs.itemById('slideCustom')
    if not custom_in:
        return
    custom = bool(custom_in.value)
    if refresh_from_spec and not custom:
        key_in = inputs.itemById('slideKey')
        key = (_slide_key_from_label(key_in.selectedItem.name)
               if key_in and key_in.selectedItem else None)
        vals = slide_ui_values({'slide_key': key, 'slide': dict(SLIDE, custom=False)})
        for cid, field in zip(_SLIDE_INPUT_IDS, _SLIDE_UI_FIELDS):
            item = inputs.itemById(cid)
            if item:
                item.value = vals[field] / 10.0
    for cid in _SLIDE_INPUT_IDS:
        item = inputs.itemById(cid)
        if item:
            item.isEnabled = custom


def add_cabinet_inputs(inputs, cfg):
    """Build the full cabinet parameter UI, pre-filled from `cfg` (mm). Only the
    essentials show by default; the rest hide behind the 'Configuracao avancada'
    toggle (their defaults come from Preferences)."""
    inputs.addValueInput('width', 'Largura (W)', 'mm', adsk.core.ValueInput.createByReal(cfg['W'] / 10.0))
    inputs.addValueInput('height', 'Altura (H)', 'mm', adsk.core.ValueInput.createByReal(cfg['H'] / 10.0))
    inputs.addValueInput('depth', 'Profundidade (D)', 'mm', adsk.core.ValueInput.createByReal(cfg['D'] / 10.0))
    inputs.addIntegerSpinnerCommandInput('shelves', 'Prateleiras', 0, 50, 1, int(cfg['n_shelves']))

    material = inputs.addDropDownCommandInput(
        'material', 'Material', adsk.core.DropDownStyles.TextListDropDownStyle)
    for (name, _thk) in get_materials():
        material.listItems.add(name, name == cfg['material'])
    if not material.selectedItem:
        material.listItems.item(0).isSelected = True

    adv_mode = inputs.addBoolValueInput('advancedMode',
                                        'Configuracao avancada (personalizar este armario)',
                                        True, '', False)
    adv_mode.tooltip = ('Mostra fundo, rodape, portas, gavetas, fita e ajustes finos. '
                        'Os padroes vem das Preferencias; ligue para personalizar so este armario.')

    inputs.addValueInput('thickness', 'Espessura', 'mm', adsk.core.ValueInput.createByReal(cfg['t'] / 10.0))
    inputs.addBoolValueInput('shelfAlignFront', 'Prateleiras alinhadas com a frente',
                             True, '', bool(cfg.get('shelf_align_front', False)))

    group = inputs.addGroupCommandInput('backGroup', 'Fundo (back panel)')
    group.isExpanded = True
    g = group.children
    g.addBoolValueInput('withBack', 'Add back panel', True, '', bool(cfg['with_back']))
    back_mode_dd = g.addDropDownCommandInput(
        'backMode', 'Fixacao do fundo', adsk.core.DropDownStyles.TextListDropDownStyle)
    _cur_back_mode = _back_mode_choice_label(cfg.get('back_mode', 'groove'))
    for (_v, lbl) in BACK_MODE_CHOICES:
        back_mode_dd.listItems.add(lbl, lbl == _cur_back_mode)
    if not back_mode_dd.selectedItem:
        back_mode_dd.listItems.item(0).isSelected = True
    back_mat = g.addDropDownCommandInput(
        'backMaterial', 'Material do fundo', adsk.core.DropDownStyles.TextListDropDownStyle)
    for (name, _thk) in get_materials():
        back_mat.listItems.add(name, name == cfg['back_material'])
    if not back_mat.selectedItem:
        back_mat.listItems.item(0).isSelected = True
    g.addValueInput('backThickness', 'Espessura do fundo', 'mm', adsk.core.ValueInput.createByReal(cfg['back_t'] / 10.0))
    g.addValueInput('dadoDepth', 'Profundidade da ranhura', 'mm', adsk.core.ValueInput.createByReal(cfg['dado_depth'] / 10.0))
    g.addValueInput('backSetback', 'Recuo do fundo', 'mm', adsk.core.ValueInput.createByReal(cfg['back_setback'] / 10.0))

    tk_group = inputs.addGroupCommandInput('toeKickGroup', 'Rodape (toe kick)')
    tk_group.isExpanded = True
    tk = tk_group.children
    tk.addBoolValueInput('withToeKick', 'Add toe kick', True, '', bool(cfg['with_toe_kick']))
    tk_mat = tk.addDropDownCommandInput(
        'toeKickMaterial', 'Material do rodape', adsk.core.DropDownStyles.TextListDropDownStyle)
    for (name, _thk) in get_materials():
        tk_mat.listItems.add(name, name == cfg['toe_kick_material'])
    if not tk_mat.selectedItem:
        tk_mat.listItems.item(0).isSelected = True
    tk.addValueInput('toeKickThickness', 'Espessura do rodape', 'mm', adsk.core.ValueInput.createByReal(cfg['toe_kick_t'] / 10.0))
    tk.addValueInput('toeKickHeight', 'Altura do rodape', 'mm', adsk.core.ValueInput.createByReal(cfg['toe_kick_height'] / 10.0))
    tk.addValueInput('toeKickSetback', 'Recuo do rodape', 'mm', adsk.core.ValueInput.createByReal(cfg['toe_kick_setback'] / 10.0))
    tk.addValueInput('toeKickMaxSpan', 'Vao max. sem reforco', 'mm', adsk.core.ValueInput.createByReal(cfg['toe_kick_max_span'] / 10.0))

    door_group = inputs.addGroupCommandInput('doorGroup', 'Portas (doors)')
    door_group.isExpanded = bool(cfg['with_doors'])
    dr = door_group.children
    dr.addBoolValueInput('withDoors', 'Add doors', True, '', bool(cfg['with_doors']))
    door_mat = dr.addDropDownCommandInput(
        'doorMaterial', 'Material da porta', adsk.core.DropDownStyles.TextListDropDownStyle)
    for (name, _thk) in get_materials():
        door_mat.listItems.add(name, name == cfg['door_material'])
    if not door_mat.selectedItem:
        door_mat.listItems.item(0).isSelected = True
    dr.addValueInput('doorThickness', 'Espessura da porta', 'mm', adsk.core.ValueInput.createByReal(cfg['door_t'] / 10.0))
    dr.addIntegerSpinnerCommandInput('nDoors', 'Numero de portas', 1, 20, 1, int(cfg['n_doors']))
    dr.addBoolValueInput('doorInset', 'Porta embutida (inset)', True, '', bool(cfg['door_inset']))
    dr.addValueInput('doorGap', 'Folga (reveal)', 'mm', adsk.core.ValueInput.createByReal(cfg['door_gap'] / 10.0))
    dr.addBoolValueInput('withHinges', 'Furacao de dobradica (cup 35mm)', True, '', bool(cfg['with_hinges']))

    dw_group = inputs.addGroupCommandInput('drawerGroup', 'Gavetas (drawers)')
    dw_group.isExpanded = bool(cfg['with_drawers'])
    dw = dw_group.children
    dw.addBoolValueInput('withDrawers', 'Add drawers', True, '', bool(cfg['with_drawers']))
    slide = dw.addDropDownCommandInput(
        'slideKey', 'Corredica (slide)', adsk.core.DropDownStyles.TextListDropDownStyle)
    for (k, desc) in slide_keys():
        slide.listItems.add(desc, k == cfg['slide_key'])
    if not slide.selectedItem:
        slide.listItems.item(0).isSelected = True
    dw.addIntegerSpinnerCommandInput('nDrawers', 'Numero de gavetas', 1, 20, 1, int(cfg['n_drawers']))
    dw.addBoolValueInput('drawerInset', 'Gaveta embutida (inset)', True, '', bool(cfg['drawer_inset']))
    dw.addValueInput('drawerGap', 'Folga (reveal)', 'mm', adsk.core.ValueInput.createByReal(cfg['drawer_gap'] / 10.0))
    dr_cfg = cfg['drawer']
    box_mat = dw.addDropDownCommandInput(
        'drawerBoxMaterial', 'Material da caixa', adsk.core.DropDownStyles.TextListDropDownStyle)
    for (name, _thk) in get_materials():
        box_mat.listItems.add(name, name == dr_cfg['box_material'])
    if not box_mat.selectedItem:
        box_mat.listItems.item(0).isSelected = True
    dw.addValueInput('drawerBoxThickness', 'Espessura da caixa', 'mm',
                     adsk.core.ValueInput.createByReal(dr_cfg['box_t'] / 10.0))
    bot_mat = dw.addDropDownCommandInput(
        'drawerBottomMaterial', 'Material do fundo', adsk.core.DropDownStyles.TextListDropDownStyle)
    for (name, _thk) in get_materials():
        bot_mat.listItems.add(name, name == dr_cfg['bottom_material'])
    if not bot_mat.selectedItem:
        bot_mat.listItems.item(0).isSelected = True
    dw.addValueInput('drawerBottomThickness', 'Espessura do fundo', 'mm',
                     adsk.core.ValueInput.createByReal(dr_cfg['bottom_t'] / 10.0))
    face_mat = dw.addDropDownCommandInput(
        'drawerFaceMaterial', 'Material da frente', adsk.core.DropDownStyles.TextListDropDownStyle)
    for (name, _thk) in get_materials():
        face_mat.listItems.add(name, name == dr_cfg['face_material'])
    if not face_mat.selectedItem:
        face_mat.listItems.item(0).isSelected = True
    dw.addValueInput('drawerFaceThickness', 'Espessura da frente', 'mm',
                     adsk.core.ValueInput.createByReal(dr_cfg['face_t'] / 10.0))
    dw.addBoolValueInput('insertRealHardware', 'Inserir modelo 3D da corredica',
                         True, '', bool(cfg['insert_real_hardware']))
    # Slide mounting measurements. Read-only readout of the chosen corredica until
    # 'Personalizar' is ticked, then they override the library spec for this
    # cabinet (see SLIDE / resolve_slide_spec).
    sl_cfg = cfg['slide']
    sl_vals = slide_ui_values(cfg)
    sl_custom = dw.addBoolValueInput('slideCustom', 'Personalizar medidas da corredica',
                                     True, '', bool(sl_cfg['custom']))
    sl_custom.tooltip = ('Ligue para editar os espacos/folgas da corredica neste armario '
                         '(sobrepoe a biblioteca de ferragens).')
    sl_ss = dw.addValueInput('slideSideSpace', 'Espaco lateral (por lado)', 'mm',
                             adsk.core.ValueInput.createByReal(sl_vals['side_space'] / 10.0))
    sl_ss.tooltip = ('Espaco que a corredica ocupa de cada lado, entre a lateral do movel '
                     'e a lateral da caixa. Lateral (roldana/telescopica) ~12,5-12,7mm; '
                     'oculta ~6,5mm. A largura da caixa = vao livre - 2x este valor.')
    dw.addValueInput('slideBottomClearance', 'Folga sob a caixa', 'mm',
                     adsk.core.ValueInput.createByReal(sl_vals['bottom_clearance'] / 10.0))
    dw.addValueInput('slideBackClearance', 'Folga no fundo (caixa-fundo)', 'mm',
                     adsk.core.ValueInput.createByReal(sl_vals['back_clearance'] / 10.0))
    dw.addValueInput('slideBoxDepth', 'Profundidade da caixa', 'mm',
                     adsk.core.ValueInput.createByReal(sl_vals['box_depth'] / 10.0))
    dw.addValueInput('slideMinDepth', 'Profundidade minima do armario', 'mm',
                     adsk.core.ValueInput.createByReal(sl_vals['min_cabinet_depth'] / 10.0))

    # Side<->base/top joinery (Fixacao Lateral). Per junction: base/top captured
    # between full-height sides ('Alinhada com base', today) or full-width with
    # the side sitting on/under it ('Sobre base'), plus an overhang (avanco). The
    # visual editor for this lives in the Cabinet Layout palette; here it's plain
    # inputs, gated behind Configuracao avancada.
    joinery = cfg.get('joinery', JOINERY)
    jn_group = inputs.addGroupCommandInput('joineryGroup', 'Fixacao lateral')
    jn_group.isExpanded = False
    jg = jn_group.children
    jbm = jg.addDropDownCommandInput(
        'joineryBottomMode', 'Base inferior', adsk.core.DropDownStyles.TextListDropDownStyle)
    for (_v, _lbl) in JOINERY_CHOICES:
        jbm.listItems.add(_lbl, _v == joinery.get('bottom_mode', 'aligned'))
    jg.addValueInput('joineryBottomOverhang', 'Base inf.: avanco da lateral', 'mm',
                     adsk.core.ValueInput.createByReal(float(joinery.get('bottom_overhang', 0.0)) / 10.0))
    jtm = jg.addDropDownCommandInput(
        'joineryTopMode', 'Base superior (tampo)', adsk.core.DropDownStyles.TextListDropDownStyle)
    for (_v, _lbl) in JOINERY_CHOICES:
        jtm.listItems.add(_lbl, _v == joinery.get('top_mode', 'aligned'))
    jg.addValueInput('joineryTopOverhang', 'Tampo: avanco da lateral', 'mm',
                     adsk.core.ValueInput.createByReal(float(joinery.get('top_overhang', 0.0)) / 10.0))
    jg.addBoolValueInput('joinerySidesToFloor', 'Laterais ate o piso (pes laterais)',
                         True, '', bool(joinery.get('sides_to_floor', False)))

    # Tamponamento (acabamento): applied finish panels over exposed faces (left /
    # right side + top). Adds to the envelope; interior unaffected. Blank material
    # inherits the carcass material. Gated behind Configuracao avancada.
    tamp = cfg.get('tamponamento', TAMPONAMENTO)
    tp_group = inputs.addGroupCommandInput('tamponamentoGroup', 'Tamponamento (acabamento)')
    tp_group.isExpanded = False
    tp = tp_group.children
    tp.addBoolValueInput('tampLeft', 'Lado esquerdo', True, '', bool(tamp.get('left', False)))
    tp.addBoolValueInput('tampRight', 'Lado direito', True, '', bool(tamp.get('right', False)))
    tp.addBoolValueInput('tampTop', 'Superior', True, '', bool(tamp.get('top', False)))
    tp.addValueInput('tampThickness', 'Espessura', 'mm',
                     adsk.core.ValueInput.createByReal(float(tamp.get('t', 18.0)) / 10.0))
    tp.addValueInput('tampFrontOverhang', 'Avanco frontal', 'mm',
                     adsk.core.ValueInput.createByReal(float(tamp.get('front_overhang', 0.0)) / 10.0))
    tp_mat = tp.addDropDownCommandInput(
        'tampMaterial', 'Material', adsk.core.DropDownStyles.TextListDropDownStyle)
    tp_mat.listItems.add('(mesmo do corpo)', not tamp.get('material'))
    for (name, _thk) in get_materials():
        tp_mat.listItems.add(name, name == tamp.get('material'))
    if not tp_mat.selectedItem:
        tp_mat.listItems.item(0).isSelected = True

    # Arremate (ajuste): scribe / gap-filler pieces to reach an uncertain ceiling
    # (sanefa superior) and/or side walls (reguas laterais). Adds to the envelope;
    # interior unaffected. Blank material inherits the carcass. Cut oversized and
    # trimmed on site. Gated behind Configuracao avancada.
    arr = cfg.get('arremate', ARREMATE)
    ar_group = inputs.addGroupCommandInput('arremateGroup', 'Arremate (ajuste ate teto/parede)')
    ar_group.isExpanded = False
    ar = ar_group.children
    ar.addBoolValueInput('arrTop', 'Sanefa superior (ate o teto)', True, '', bool(arr.get('top', False)))
    # Gap input mode: enter the measured gap directly, or the room's total ceiling
    # height and have the gap computed (ceiling_height - H). Only one field shows.
    ar_mode = ar.addDropDownCommandInput(
        'arrTopMode', 'Medida da sanefa', adsk.core.DropDownStyles.TextListDropDownStyle)
    _cur_arr_mode = _arremate_top_mode_label(arr.get('top_gap_mode', 'gap'))
    for (_v, _lbl) in ARREMATE_TOP_MODE_CHOICES:
        ar_mode.listItems.add(_lbl, _lbl == _cur_arr_mode)
    ar.addValueInput('arrTopGap', 'Folga ate o teto', 'mm',
                     adsk.core.ValueInput.createByReal(float(arr.get('top_gap', 50.0)) / 10.0))
    ar.addValueInput('arrCeilingHeight', 'Altura do teto (piso->teto)', 'mm',
                     adsk.core.ValueInput.createByReal(float(arr.get('ceiling_height', 2400.0)) / 10.0))
    ar.addBoolValueInput('arrTopInline', 'Sanefa faceada com as frentes', True, '',
                         bool(arr.get('top_inline_fronts', False)))
    ar.addBoolValueInput('arrTopUShape', 'Sanefa em U (retornos laterais)', True, '',
                         bool(arr.get('top_side_returns', True)))
    ar.addBoolValueInput('arrLeft', 'Regua esquerda (ate a parede)', True, '', bool(arr.get('left', False)))
    ar.addBoolValueInput('arrRight', 'Regua direita (ate a parede)', True, '', bool(arr.get('right', False)))
    ar.addValueInput('arrSideGap', 'Folga ate a parede (cada lado)', 'mm',
                     adsk.core.ValueInput.createByReal(float(arr.get('side_gap', 30.0)) / 10.0))
    ar.addValueInput('arrThickness', 'Espessura', 'mm',
                     adsk.core.ValueInput.createByReal(float(arr.get('t', 18.0)) / 10.0))
    ar_mat = ar.addDropDownCommandInput(
        'arrMaterial', 'Material', adsk.core.DropDownStyles.TextListDropDownStyle)
    ar_mat.listItems.add('(mesmo do corpo)', not arr.get('material'))
    for (name, _thk) in get_materials():
        ar_mat.listItems.add(name, name == arr.get('material'))
    if not ar_mat.selectedItem:
        ar_mat.listItems.item(0).isSelected = True

    # Puxador integrado (frente estendida): handleless fronts. The doors/drawer
    # faces that reach the cabinet's bottom (or top) edge are cut longer, and the
    # protruding lip is what the user pulls. Overlay fronts only; off by default.
    # Gated behind Configuracao avancada.
    px = cfg.get('puxador', PUXADOR)
    px_group = inputs.addGroupCommandInput('puxadorGroup', 'Puxador integrado (frente estendida)')
    px_group.isExpanded = bool(px.get('enabled', False))
    pxg = px_group.children
    px_on = pxg.addBoolValueInput('puxadorEnabled', 'Sem puxador (frente estendida)',
                                  True, '', bool(px.get('enabled', False)))
    px_on.tooltip = ('Dispensa o puxador: a frente passa da borda do armario e a aba '
                     'que sobra serve de pegada. So vale para frentes sobrepostas.')
    px_side = pxg.addDropDownCommandInput(
        'puxadorSide', 'Lado da pegada', adsk.core.DropDownStyles.TextListDropDownStyle)
    _cur_px_side = _puxador_side_label(px.get('side', 'bottom'))
    for (_v, _lbl) in PUXADOR_SIDE_CHOICES:
        px_side.listItems.add(_lbl, _lbl == _cur_px_side)
    if not px_side.selectedItem:
        px_side.listItems.item(0).isSelected = True
    px_size = pxg.addValueInput('puxadorSize', 'Altura da aba (pegada)', 'mm',
                                adsk.core.ValueInput.createByReal(float(px.get('size', 40.0)) / 10.0))
    px_size.tooltip = ('Quanto a frente avanca alem da borda do armario. Com rodape a aba '
                       'e limitada a altura do rodape para nao passar do piso.')

    # Edge banding (fita) — its own group (not buried in Advanced), since which
    # edges get taped and how thick is a primary cut-list decision. Two editable
    # tape names (0.4mm / 1mm) plus the thickness per part group: 'carcass' = the
    # visible front edge of sides/base/top/shelves/dividers; 'fronts' = doors +
    # drawer faces (all four edges) + the toe-kick front board.
    fita = cfg['fita']
    fita_group = inputs.addGroupCommandInput('fitaGroup', 'Fita (fita de borda)')
    fita_group.isExpanded = True
    fg = fita_group.children
    fc = fg.addDropDownCommandInput(
        'fitaCarcass', 'Bordas do corpo', adsk.core.DropDownStyles.TextListDropDownStyle)
    for (_v, lbl) in FITA_CHOICES:
        fc.listItems.add(lbl, lbl == _fita_choice_label(fita['carcass']))
    ff = fg.addDropDownCommandInput(
        'fitaFronts', 'Frentes (portas/gavetas/rodape)',
        adsk.core.DropDownStyles.TextListDropDownStyle)
    for (_v, lbl) in FITA_CHOICES:
        ff.listItems.add(lbl, lbl == _fita_choice_label(fita['fronts']))
    fg.addStringValueInput('fitaThin', 'Nome da fita 0.4mm', fita['name_thin'])
    fg.addStringValueInput('fitaThick', 'Nome da fita 1mm', fita['name_thick'])

    adv = inputs.addGroupCommandInput('advGroup', 'Advanced')
    adv.isExpanded = False
    a = adv.children
    tol = cfg['tol']
    a.addValueInput('tolDadoBottom', 'Folga fundo da ranhura', 'mm',
                    adsk.core.ValueInput.createByReal(tol['dado_bottom_clearance'] / 10.0))
    a.addValueInput('tolDadoSide', 'Folga lateral da ranhura', 'mm',
                    adsk.core.ValueInput.createByReal(tol['dado_side_clearance'] / 10.0))
    a.addValueInput('tolShelfBack', 'Folga prateleira-fundo', 'mm',
                    adsk.core.ValueInput.createByReal(tol['shelf_back_gap'] / 10.0))
    a.addValueInput('tolShelfFront', 'Recuo frontal (prateleira recuada)', 'mm',
                    adsk.core.ValueInput.createByReal(tol['shelf_front_setback'] / 10.0))
    a.addValueInput('tolShelfDoor', 'Folga prateleira-porta', 'mm',
                    adsk.core.ValueInput.createByReal(tol['shelf_door_clearance'] / 10.0))

    # Hinge boring dimensions (concealed cup). The plate screw pilot specifics
    # (diameter/depth/spacing) stay at the HINGE defaults and are not exposed.
    hinge = cfg.get('hinge', HINGE)
    a.addValueInput('hingeCupDia', 'Dobradica: diametro do caneco', 'mm',
                    adsk.core.ValueInput.createByReal(hinge['cup_diameter'] / 10.0))
    a.addValueInput('hingeCupDepth', 'Dobradica: profundidade do caneco', 'mm',
                    adsk.core.ValueInput.createByReal(hinge['cup_depth'] / 10.0))
    a.addValueInput('hingeCupEdge', 'Dobradica: borda ao centro do caneco', 'mm',
                    adsk.core.ValueInput.createByReal(hinge['cup_edge'] / 10.0))
    a.addValueInput('hingeEndInset', 'Dobradica: recuo das pontas', 'mm',
                    adsk.core.ValueInput.createByReal(hinge['end_inset'] / 10.0))
    a.addValueInput('hingeShelfClear', 'Dobradica: folga da prateleira', 'mm',
                    adsk.core.ValueInput.createByReal(hinge['shelf_clearance'] / 10.0))

    # Start collapsed/clean; the advancedMode toggle reveals everything above.
    _apply_cabinet_advanced_visibility(inputs, adv_mode.value)
    # Show only the arremate gap field matching the chosen measurement mode.
    _apply_arremate_top_mode_visibility(inputs)
    # Lock the slide measurements unless 'Personalizar' is on.
    _apply_slide_custom_state(inputs)


def read_cabinet_inputs(inputs):
    """Read the cabinet parameter UI into a config dict (mm)."""
    # Start from the HINGE defaults so the un-exposed pilot specifics are kept,
    # then override the knobs shown in Advanced.
    hinge = dict(HINGE)
    hinge.update({
        'cup_diameter': inputs.itemById('hingeCupDia').value * 10.0,
        'cup_depth': inputs.itemById('hingeCupDepth').value * 10.0,
        'cup_edge': inputs.itemById('hingeCupEdge').value * 10.0,
        'end_inset': inputs.itemById('hingeEndInset').value * 10.0,
        'shelf_clearance': inputs.itemById('hingeShelfClear').value * 10.0,
    })
    return {
        'W': inputs.itemById('width').value * 10.0,
        'H': inputs.itemById('height').value * 10.0,
        'D': inputs.itemById('depth').value * 10.0,
        't': inputs.itemById('thickness').value * 10.0,
        'n_shelves': inputs.itemById('shelves').value,
        'shelf_align_front': inputs.itemById('shelfAlignFront').value,
        'material': inputs.itemById('material').selectedItem.name,
        'with_back': inputs.itemById('withBack').value,
        'back_mode': _back_mode_choice_value(inputs.itemById('backMode').selectedItem.name),
        'back_material': inputs.itemById('backMaterial').selectedItem.name,
        'back_t': inputs.itemById('backThickness').value * 10.0,
        'dado_depth': inputs.itemById('dadoDepth').value * 10.0,
        'back_setback': inputs.itemById('backSetback').value * 10.0,
        'with_toe_kick': inputs.itemById('withToeKick').value,
        'toe_kick_material': inputs.itemById('toeKickMaterial').selectedItem.name,
        'toe_kick_t': inputs.itemById('toeKickThickness').value * 10.0,
        'toe_kick_height': inputs.itemById('toeKickHeight').value * 10.0,
        'toe_kick_setback': inputs.itemById('toeKickSetback').value * 10.0,
        'toe_kick_max_span': inputs.itemById('toeKickMaxSpan').value * 10.0,
        'with_doors': inputs.itemById('withDoors').value,
        'door_material': inputs.itemById('doorMaterial').selectedItem.name,
        'door_t': inputs.itemById('doorThickness').value * 10.0,
        'n_doors': inputs.itemById('nDoors').value,
        'door_inset': inputs.itemById('doorInset').value,
        'door_gap': inputs.itemById('doorGap').value * 10.0,
        'with_hinges': inputs.itemById('withHinges').value,
        'hinge': hinge,
        'with_drawers': inputs.itemById('withDrawers').value,
        'n_drawers': inputs.itemById('nDrawers').value,
        'drawer_inset': inputs.itemById('drawerInset').value,
        'drawer_gap': inputs.itemById('drawerGap').value * 10.0,
        'slide_key': _slide_key_from_label(inputs.itemById('slideKey').selectedItem.name),
        'insert_real_hardware': inputs.itemById('insertRealHardware').value,
        # Start from the DRAWER defaults so the un-exposed box specs (bottom dado,
        # box height, top gap) are kept, then override what the dialog shows.
        # (The drawer face band now comes from the shared 'fita' block, not a
        # per-drawer field.)
        'drawer': dict(DRAWER, **{
            'box_material': inputs.itemById('drawerBoxMaterial').selectedItem.name,
            'box_t': inputs.itemById('drawerBoxThickness').value * 10.0,
            'bottom_material': inputs.itemById('drawerBottomMaterial').selectedItem.name,
            'bottom_t': inputs.itemById('drawerBottomThickness').value * 10.0,
            'face_material': inputs.itemById('drawerFaceMaterial').selectedItem.name,
            'face_t': inputs.itemById('drawerFaceThickness').value * 10.0,
        }),
        # Slide measurements. Always stored; only honoured while 'custom' is on
        # (otherwise the library spec for slide_key wins — see resolve_slide_spec).
        'slide': {
            'custom': inputs.itemById('slideCustom').value,
            'side_space': inputs.itemById('slideSideSpace').value * 10.0,
            'bottom_clearance': inputs.itemById('slideBottomClearance').value * 10.0,
            'back_clearance': inputs.itemById('slideBackClearance').value * 10.0,
            'box_depth': inputs.itemById('slideBoxDepth').value * 10.0,
            'min_cabinet_depth': inputs.itemById('slideMinDepth').value * 10.0,
        },
        'tol': {
            'dado_bottom_clearance': inputs.itemById('tolDadoBottom').value * 10.0,
            'dado_side_clearance': inputs.itemById('tolDadoSide').value * 10.0,
            'shelf_back_gap': inputs.itemById('tolShelfBack').value * 10.0,
            'shelf_front_setback': inputs.itemById('tolShelfFront').value * 10.0,
            'shelf_door_clearance': inputs.itemById('tolShelfDoor').value * 10.0,
        },
        'fita': {
            'name_thin': inputs.itemById('fitaThin').value,
            'name_thick': inputs.itemById('fitaThick').value,
            'carcass': _fita_choice_value(inputs.itemById('fitaCarcass').selectedItem.name),
            'fronts': _fita_choice_value(inputs.itemById('fitaFronts').selectedItem.name),
        },
        'joinery': {
            'bottom_mode': _joinery_choice_value(inputs.itemById('joineryBottomMode').selectedItem.name),
            'bottom_overhang': inputs.itemById('joineryBottomOverhang').value * 10.0,
            'top_mode': _joinery_choice_value(inputs.itemById('joineryTopMode').selectedItem.name),
            'top_overhang': inputs.itemById('joineryTopOverhang').value * 10.0,
            'sides_to_floor': inputs.itemById('joinerySidesToFloor').value,
        },
        'tamponamento': {
            'left': inputs.itemById('tampLeft').value,
            'right': inputs.itemById('tampRight').value,
            'top': inputs.itemById('tampTop').value,
            't': inputs.itemById('tampThickness').value * 10.0,
            'front_overhang': inputs.itemById('tampFrontOverhang').value * 10.0,
            # First list item ('(mesmo do corpo)') means inherit -> store ''.
            'material': ('' if inputs.itemById('tampMaterial').selectedItem.name == '(mesmo do corpo)'
                         else inputs.itemById('tampMaterial').selectedItem.name),
        },
        'arremate': {
            'top': inputs.itemById('arrTop').value,
            'top_gap_mode': _arremate_top_mode_value(inputs.itemById('arrTopMode').selectedItem.name),
            'top_gap': inputs.itemById('arrTopGap').value * 10.0,
            'ceiling_height': inputs.itemById('arrCeilingHeight').value * 10.0,
            'top_inline_fronts': inputs.itemById('arrTopInline').value,
            'top_side_returns': inputs.itemById('arrTopUShape').value,
            'left': inputs.itemById('arrLeft').value,
            'right': inputs.itemById('arrRight').value,
            'side_gap': inputs.itemById('arrSideGap').value * 10.0,
            't': inputs.itemById('arrThickness').value * 10.0,
            'material': ('' if inputs.itemById('arrMaterial').selectedItem.name == '(mesmo do corpo)'
                         else inputs.itemById('arrMaterial').selectedItem.name),
        },
        'puxador': {
            'enabled': inputs.itemById('puxadorEnabled').value,
            'side': _puxador_side_value(inputs.itemById('puxadorSide').selectedItem.name),
            'size': inputs.itemById('puxadorSize').value * 10.0,
        },
    }


def write_cabinet_inputs(inputs, cfg):
    """Push a config dict (mm) back into the existing cabinet parameter UI."""
    inputs.itemById('width').value = cfg['W'] / 10.0
    inputs.itemById('height').value = cfg['H'] / 10.0
    inputs.itemById('depth').value = cfg['D'] / 10.0
    inputs.itemById('thickness').value = cfg['t'] / 10.0
    inputs.itemById('shelves').value = int(cfg['n_shelves'])
    inputs.itemById('shelfAlignFront').value = bool(cfg.get('shelf_align_front', False))
    _select_dropdown(inputs.itemById('material'), cfg['material'])
    inputs.itemById('withBack').value = bool(cfg['with_back'])
    _select_dropdown(inputs.itemById('backMode'), _back_mode_choice_label(cfg.get('back_mode', 'groove')))
    _select_dropdown(inputs.itemById('backMaterial'), cfg['back_material'])
    inputs.itemById('backThickness').value = cfg['back_t'] / 10.0
    inputs.itemById('dadoDepth').value = cfg['dado_depth'] / 10.0
    inputs.itemById('backSetback').value = cfg['back_setback'] / 10.0
    inputs.itemById('withToeKick').value = bool(cfg['with_toe_kick'])
    _select_dropdown(inputs.itemById('toeKickMaterial'), cfg['toe_kick_material'])
    inputs.itemById('toeKickThickness').value = cfg['toe_kick_t'] / 10.0
    inputs.itemById('toeKickHeight').value = cfg['toe_kick_height'] / 10.0
    inputs.itemById('toeKickSetback').value = cfg['toe_kick_setback'] / 10.0
    inputs.itemById('toeKickMaxSpan').value = cfg['toe_kick_max_span'] / 10.0
    inputs.itemById('withDoors').value = bool(cfg['with_doors'])
    _select_dropdown(inputs.itemById('doorMaterial'), cfg['door_material'])
    inputs.itemById('doorThickness').value = cfg['door_t'] / 10.0
    inputs.itemById('nDoors').value = int(cfg['n_doors'])
    inputs.itemById('doorInset').value = bool(cfg['door_inset'])
    inputs.itemById('doorGap').value = cfg['door_gap'] / 10.0
    inputs.itemById('withHinges').value = bool(cfg['with_hinges'])
    inputs.itemById('withDrawers').value = bool(cfg['with_drawers'])
    _select_dropdown(inputs.itemById('slideKey'), _slide_label_for_key(cfg['slide_key']))
    inputs.itemById('nDrawers').value = int(cfg['n_drawers'])
    inputs.itemById('drawerInset').value = bool(cfg['drawer_inset'])
    inputs.itemById('drawerGap').value = cfg['drawer_gap'] / 10.0
    dr_cfg = cfg['drawer']
    _select_dropdown(inputs.itemById('drawerBoxMaterial'), dr_cfg['box_material'])
    inputs.itemById('drawerBoxThickness').value = dr_cfg['box_t'] / 10.0
    _select_dropdown(inputs.itemById('drawerBottomMaterial'), dr_cfg['bottom_material'])
    inputs.itemById('drawerBottomThickness').value = dr_cfg['bottom_t'] / 10.0
    _select_dropdown(inputs.itemById('drawerFaceMaterial'), dr_cfg['face_material'])
    inputs.itemById('drawerFaceThickness').value = dr_cfg['face_t'] / 10.0
    inputs.itemById('insertRealHardware').value = bool(cfg['insert_real_hardware'])
    inputs.itemById('slideCustom').value = bool(cfg['slide']['custom'])
    sl_vals = slide_ui_values(cfg)
    for cid, field in zip(_SLIDE_INPUT_IDS, _SLIDE_UI_FIELDS):
        inputs.itemById(cid).value = sl_vals[field] / 10.0
    _apply_slide_custom_state(inputs)
    tol = cfg['tol']
    inputs.itemById('tolDadoBottom').value = tol['dado_bottom_clearance'] / 10.0
    inputs.itemById('tolDadoSide').value = tol['dado_side_clearance'] / 10.0
    inputs.itemById('tolShelfBack').value = tol['shelf_back_gap'] / 10.0
    inputs.itemById('tolShelfFront').value = tol['shelf_front_setback'] / 10.0
    inputs.itemById('tolShelfDoor').value = tol['shelf_door_clearance'] / 10.0
    hinge = cfg.get('hinge', HINGE)
    inputs.itemById('hingeCupDia').value = hinge['cup_diameter'] / 10.0
    inputs.itemById('hingeCupDepth').value = hinge['cup_depth'] / 10.0
    inputs.itemById('hingeCupEdge').value = hinge['cup_edge'] / 10.0
    inputs.itemById('hingeEndInset').value = hinge['end_inset'] / 10.0
    inputs.itemById('hingeShelfClear').value = hinge['shelf_clearance'] / 10.0
    fita = cfg['fita']
    inputs.itemById('fitaThin').value = fita['name_thin']
    inputs.itemById('fitaThick').value = fita['name_thick']
    _select_dropdown(inputs.itemById('fitaCarcass'), _fita_choice_label(fita['carcass']))
    _select_dropdown(inputs.itemById('fitaFronts'), _fita_choice_label(fita['fronts']))
    joinery = cfg.get('joinery', JOINERY)
    _select_dropdown(inputs.itemById('joineryBottomMode'),
                     _joinery_choice_label(joinery.get('bottom_mode', 'aligned')))
    inputs.itemById('joineryBottomOverhang').value = float(joinery.get('bottom_overhang', 0.0)) / 10.0
    _select_dropdown(inputs.itemById('joineryTopMode'),
                     _joinery_choice_label(joinery.get('top_mode', 'aligned')))
    inputs.itemById('joineryTopOverhang').value = float(joinery.get('top_overhang', 0.0)) / 10.0
    inputs.itemById('joinerySidesToFloor').value = bool(joinery.get('sides_to_floor', False))
    tamp = cfg.get('tamponamento', TAMPONAMENTO)
    inputs.itemById('tampLeft').value = bool(tamp.get('left', False))
    inputs.itemById('tampRight').value = bool(tamp.get('right', False))
    inputs.itemById('tampTop').value = bool(tamp.get('top', False))
    inputs.itemById('tampThickness').value = float(tamp.get('t', 18.0)) / 10.0
    inputs.itemById('tampFrontOverhang').value = float(tamp.get('front_overhang', 0.0)) / 10.0
    _select_dropdown(inputs.itemById('tampMaterial'),
                     tamp.get('material') or '(mesmo do corpo)')
    arr = cfg.get('arremate', ARREMATE)
    inputs.itemById('arrTop').value = bool(arr.get('top', False))
    _select_dropdown(inputs.itemById('arrTopMode'),
                     _arremate_top_mode_label(arr.get('top_gap_mode', 'gap')))
    inputs.itemById('arrTopGap').value = float(arr.get('top_gap', 50.0)) / 10.0
    inputs.itemById('arrCeilingHeight').value = float(arr.get('ceiling_height', 2400.0)) / 10.0
    inputs.itemById('arrTopInline').value = bool(arr.get('top_inline_fronts', False))
    inputs.itemById('arrTopUShape').value = bool(arr.get('top_side_returns', True))
    inputs.itemById('arrLeft').value = bool(arr.get('left', False))
    inputs.itemById('arrRight').value = bool(arr.get('right', False))
    inputs.itemById('arrSideGap').value = float(arr.get('side_gap', 30.0)) / 10.0
    inputs.itemById('arrThickness').value = float(arr.get('t', 18.0)) / 10.0
    _select_dropdown(inputs.itemById('arrMaterial'),
                     arr.get('material') or '(mesmo do corpo)')
    px = cfg.get('puxador', PUXADOR)
    inputs.itemById('puxadorEnabled').value = bool(px.get('enabled', False))
    _select_dropdown(inputs.itemById('puxadorSide'), _puxador_side_label(px.get('side', 'bottom')))
    inputs.itemById('puxadorSize').value = float(px.get('size', 40.0)) / 10.0


def validate_cfg(cfg):
    """Return an error message string if the config is invalid, else None."""
    cfg = normalize_cfg(cfg)   # ensure a layout is present (synth from flat if needed)
    W, H, D, t = cfg['W'], cfg['H'], cfg['D'], cfg['t']
    if W <= 2 * t or H <= 2 * t:
        return 'Largura and Altura must be larger than twice the thickness.'
    if cfg['with_back']:
        bt = cfg['back_t']
        if bt <= 0:
            return 'Back panel thickness must be greater than 0.'
        # Groove (encaixado) mode only: the dado geometry must fit the depth. An
        # overlay (sobreposto) back is applied flush to the rear and extends
        # behind, so it has no grooves and consumes no interior depth.
        if cfg.get('back_mode', 'groove') != 'overlay':
            sb = cfg['back_setback']
            dd = cfg['dado_depth']
            bc = cfg['tol']['dado_bottom_clearance']
            sc = cfg['tol']['dado_side_clearance']
            if dd <= 0 or dd >= t:
                return 'Ranhura depth must be > 0 and less than the carcass thickness ({0:.0f}mm).'.format(t)
            if bc < 0 or bc >= dd:
                return 'Folga do fundo da ranhura must be >= 0 and less than the ranhura depth ({0:.1f}mm).'.format(dd)
            if sc < 0 or sb < sc:
                return 'Folga lateral da ranhura must be >= 0 and no larger than the back setback.'
            if sb < 0 or sb + bt > D:
                return 'Back panel (recuo + espessura) does not fit within the depth.'
    if cfg['with_toe_kick']:
        kkh, kks, kkt = cfg['toe_kick_height'], cfg['toe_kick_setback'], cfg['toe_kick_t']
        kms = cfg['toe_kick_max_span']
        if kkh <= 0:
            return 'Altura do rodape must be greater than 0.'
        if kkh >= H - 2 * t:
            return 'Altura do rodape leaves no room for the carcass (must be < H - 2*espessura).'
        if kkt <= 0:
            return 'Espessura do rodape must be greater than 0.'
        if kks < 0 or kks + 2 * kkt >= D:
            return ('Rodape does not fit within the depth: front board + back rail '
                    '(recuo + 2x espessura) must be less than the profundidade.')
        if kms <= 0:
            return 'Vao max. sem reforco do rodape must be greater than 0.'
    # Side<->base/top joinery (Fixacao Lateral): valid modes + non-negative overhangs.
    jn = cfg['joinery']
    for side in ('bottom', 'top'):
        if jn.get(side + '_mode') not in ('aligned', 'over'):
            return "Fixacao lateral: modo invalido (use 'aligned' ou 'over')."
        if float(jn.get(side + '_overhang', 0.0)) < 0:
            return 'Fixacao lateral: o avanco (overhang) deve ser >= 0.'
    # Tamponamento (acabamento): a positive thickness is required when any face is on.
    tp = cfg.get('tamponamento', {})
    if tp.get('left') or tp.get('right') or tp.get('top'):
        if float(tp.get('t', 0.0)) <= 0:
            return 'Espessura do tamponamento deve ser maior que zero.'
        if float(tp.get('front_overhang', 0.0)) < 0:
            return 'Avanco frontal do tamponamento deve ser >= 0.'
    # Arremate (ajuste): positive thickness + a positive gap for each enabled piece.
    ar = cfg.get('arremate', {})
    if ar.get('left') or ar.get('right') or ar.get('top'):
        if float(ar.get('t', 0.0)) <= 0:
            return 'Espessura do arremate deve ser maior que zero.'
    if ar.get('top'):
        if ar.get('top_gap_mode') == 'ceiling':
            if float(ar.get('ceiling_height', 0.0)) <= float(cfg.get('H', 0.0)):
                return ('Altura do teto deve ser maior que a altura do armario '
                        '(para haver folga da sanefa).')
        elif float(ar.get('top_gap', 0.0)) <= 0:
            return 'Folga do arremate superior (ate o teto) deve ser maior que zero.'
    if (ar.get('left') or ar.get('right')) and float(ar.get('side_gap', 0.0)) <= 0:
        return 'Folga do arremate lateral (ate a parede) deve ser maior que zero.'
    # Puxador integrado (frente estendida): a positive lip, a known side, and no
    # other piece already occupying the space the lip grows into. A TOP lip lives
    # in front of the tampo (y < 0), exactly where a faced sanefa / a forward
    # tamponamento superior sit — those combinations would share volume.
    px = cfg.get('puxador', {})
    if px.get('enabled'):
        if px.get('side', 'bottom') not in ('bottom', 'top'):
            return "Puxador: lado invalido (use 'bottom' ou 'top')."
        if float(px.get('size', 0.0) or 0.0) <= 0:
            return 'Altura da aba do puxador deve ser maior que zero.'
        if px.get('side') == 'top' and layout_has_overlay_fronts(cfg['layout']):
            if ar.get('top') and ar.get('top_inline_fronts'):
                return ('Puxador com aba superior nao combina com a sanefa faceada com as '
                        'frentes: as duas ocupam o mesmo espaco a frente do tampo.')
            if tp.get('top') and float(tp.get('front_overhang', 0.0)) > 0:
                return ('Puxador com aba superior nao combina com tamponamento superior '
                        'com avanco frontal: as pecas se sobrepoem.')
    # Interior layout: walk the region tree (same planner the builder uses, so a
    # split that validates always builds) and check that each leaf fits its band.
    kick_h = cfg['toe_kick_height'] if cfg['with_toe_kick'] else 0.0
    Hbox = H - kick_h
    if Hbox - 2 * t <= 0:
        return 'Altura leaves no clear interior height for the carcass.'
    Wc, Hbox_c, tc = W / 10.0, Hbox / 10.0, t / 10.0
    z_off = kick_h / 10.0
    back_front_y = (D - cfg['back_setback'] - cfg['back_t']) \
        if (cfg['with_back'] and cfg.get('back_mode', 'groove') != 'overlay') else D
    root_band = _Band(tc, Wc - tc, z_off + tc, z_off + Hbox_c - tc,
                      tc, tc, tc, tc, 'L', 'R')
    try:
        leaves, _dividers = plan_layout(root_band, cfg['layout'], tc, back_front_y / 10.0)
    except ValueError as e:
        return str(e)
    for band, node, _prefix in leaves:
        err = _validate_leaf(band, node, cfg)
        if err:
            return err
    return None


def _validate_leaf(band, node, cfg):
    """Check one leaf fits its band (mm). Ports the classic per-type fit rules,
    reading the band's clear (inset) or overlay-extended (sobreposta) dimensions."""
    typ = node.get('type', 'open')
    W, H, D, t = cfg['W'], cfg['H'], cfg['D'], cfg['t']
    band_w_mm = (band.x1 - band.x0) * 10.0
    band_h_mm = (band.z1 - band.z0) * 10.0
    back_front_y = (D - cfg['back_setback'] - cfg['back_t']) \
        if (cfg['with_back'] and cfg.get('back_mode', 'groove') != 'overlay') else D

    if typ == 'shelves':
        n = node['count']
        if (band_h_mm - n * t) / (n + 1) <= 0:
            return ('Too many shelves for a region {0:.0f}mm tall. Reduce the shelf '
                    'count or split the region.'.format(band_h_mm))
        align = node.get('shelf_align_front')
        if align is None:
            align = cfg.get('shelf_align_front', False)
        fs = 0.0 if align else cfg['tol']['shelf_front_setback']
        depth = (back_front_y - cfg['tol']['shelf_back_gap'] - fs) if cfg['with_back'] else (D - fs)
        if depth <= 0:
            return 'Shelf depth is non-positive; reduce the back setback/gaps or deepen the cabinet.'
        return None

    if typ == 'doors':
        n = node['count']
        gap = cfg['door_gap'] if node.get('gap') is None else node['gap']
        dt = cfg['door_t']
        inset = node.get('inset', False)
        if inset:
            region_w, region_h = band_w_mm, band_h_mm
        else:
            region_w = ((band.x1 + band.ext_r) - (band.x0 - band.ext_l)) * 10.0
            region_h = ((band.z1 + band.ext_t) - (band.z0 - band.ext_b)) * 10.0
        if dt <= 0:
            return 'Espessura da porta must be greater than 0.'
        if gap < 0:
            return 'Folga da porta must be >= 0.'
        if (region_w - (n + 1) * gap) / n <= 0:
            return ('Doors do not fit: {0} door(s) plus the reveal gaps exceed the '
                    'region width. Reduce the door count or the folga.'.format(n))
        if region_h - 2 * gap <= 0:
            return 'Folga da porta is too large for this region height.'
        if cfg['with_hinges']:
            hinge = cfg.get('hinge', HINGE)
            if hinge['cup_diameter'] <= 0 or hinge['cup_depth'] <= 0 or hinge['cup_edge'] <= 0:
                return 'Hinge cup diameter, depth and edge distance must all be > 0.'
            if hinge['cup_depth'] >= dt:
                return ('Hinge cup depth ({0:.0f}mm) must be less than the door '
                        'thickness ({1:.0f}mm), or the bore goes through the door.'.format(
                            hinge['cup_depth'], dt))
            if hinge['shelf_clearance'] < 0 or hinge['end_inset'] < 0:
                return 'Hinge shelf clearance and end inset must be >= 0.'
            door_w_mm = (region_w - (n + 1) * gap) / n
            need = hinge['cup_edge'] + hinge['cup_diameter'] / 2.0
            if door_w_mm < need:
                return ('Doors are too narrow for the hinge cup: each door is '
                        '{0:.0f}mm but the cup needs at least {1:.0f}mm. Reduce the '
                        'door count or the cup size.'.format(door_w_mm, need))
        return None

    if typ == 'drawers':
        n = node['count']
        gap = cfg['drawer_gap'] if node.get('gap') is None else node['gap']
        drawer = cfg['drawer']
        spec = resolve_slide_spec({'slide_key': node.get('slide_key') or cfg['slide_key'],
                                   'slide': cfg.get('slide')})
        inset = node.get('inset', False)
        if inset:
            region_w, region_h = band_w_mm, band_h_mm
        else:
            region_w = ((band.x1 + band.ext_r) - (band.x0 - band.ext_l)) * 10.0
            region_h = ((band.z1 + band.ext_t) - (band.z0 - band.ext_b)) * 10.0
        region_inner_w = band_w_mm   # clear width the box actually fits in
        if gap < 0:
            return 'Folga da gaveta must be >= 0.'
        if not spec.get('key'):
            return 'Selecione uma corredica (slide) valida.'
        if (region_h - (n + 1) * gap) / n <= 0:
            return ('Gavetas do not fit: {0} drawer(s) plus the reveal gaps exceed the '
                    'region height. Reduce the drawer count or the folga.'.format(n))
        if region_w - 2 * gap <= 0:
            return 'Folga da gaveta is too large for this region width.'
        face_h = (region_h - (n + 1) * gap) / n
        if face_h - spec['bottom_clearance'] - drawer['box_top_gap'] <= 0:
            return ('Gavetas do not fit: with the runner gap and the top clearance '
                    'there is no room for the drawer box. Reduce the drawer count.')
        if D < spec['min_cabinet_depth']:
            return ('Profundidade {0:.0f}mm is less than the slide minimum {1:.0f}mm. '
                    'Increase D or choose a shorter slide.'.format(D, spec['min_cabinet_depth']))
        if (back_front_y - spec['back_clearance']) < 100.0:
            return ('Not enough depth for the drawer box (increase the profundidade '
                    'or reduce the back setback).')
        # Width: the runner takes side_space per side (12.5-12.7mm for a lateral
        # roldana/telescopica, ~6.5mm for an oculta), so the box is that much
        # narrower than the clear opening.
        side_space = slide_side_space(spec, drawer['box_t'])
        if side_space < 0:
            return ('Corredica invalida: o espaco lateral ficou negativo. Ajuste o '
                    'espaco lateral da corredica ou a espessura da caixa.')
        box_outer_w = region_inner_w - 2 * side_space
        if box_outer_w <= 2 * drawer['box_t']:
            return ('This region is too narrow for the slide: with {0:.1f}mm per side '
                    'for the corredica the drawer box has no width left. Widen the '
                    'region, choose a slimmer slide or thinner box sides.'.format(side_space))
        return None

    if typ == 'blind':
        gap = cfg['door_gap'] if node.get('gap') is None else node['gap']
        inset = node.get('inset', False)
        if inset:
            region_w, region_h = band_w_mm, band_h_mm
        else:
            region_w = ((band.x1 + band.ext_r) - (band.x0 - band.ext_l)) * 10.0
            region_h = ((band.z1 + band.ext_t) - (band.z0 - band.ext_b)) * 10.0
        if cfg['door_t'] <= 0:
            return 'Espessura da frente (painel cego) must be greater than 0.'
        if gap < 0:
            return 'Folga do painel cego must be >= 0.'
        if region_w - 2 * gap <= 0 or region_h - 2 * gap <= 0:
            return ('A folga é maior que a região para o painel cego. Reduza a '
                    'folga ou aumente a região.')
        return None

    return None  # 'open'


def collect_cabinets(design):
    """All top-level cabinets (occurrence, config) that carry a stored config."""
    out = []
    for occ in design.rootComponent.occurrences:
        attr = occ.component.attributes.itemByName(ATTR_GROUP, CABINET_CFG_ATTR)
        if attr and attr.value:
            try:
                out.append((occ, normalize_cfg(json.loads(attr.value))))
            except (ValueError, TypeError):
                continue
    return out


class NewCabinetCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            add_cabinet_inputs(args.command.commandInputs, effective_default_cfg())
            onChange = NewCabinetInputChangedHandler()
            args.command.inputChanged.add(onChange)
            handlers.append(onChange)
            execHandler = NewCabinetExecuteHandler()
            args.command.execute.add(execHandler)
            handlers.append(execHandler)
        except:
            if ui:
                ui.messageBox('New Cabinet setup failed:\n{}'.format(traceback.format_exc()))


class NewCabinetInputChangedHandler(adsk.core.InputChangedEventHandler):
    def notify(self, args):
        try:
            if args.input.id == 'advancedMode':
                _apply_cabinet_advanced_visibility(args.inputs, args.input.value)
            elif args.input.id == 'arrTopMode':
                _apply_arremate_top_mode_visibility(args.inputs)
            elif args.input.id in ('slideKey', 'slideCustom'):
                _apply_slide_custom_state(args.inputs,
                                          refresh_from_spec=(args.input.id == 'slideKey'))
        except:
            if ui:
                ui.messageBox('New Cabinet input failed:\n{}'.format(traceback.format_exc()))


class NewCabinetExecuteHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            design = get_design()
            if not design:
                ui.messageBox('Open a Design document first.')
                return

            cfg = read_cabinet_inputs(args.command.commandInputs)
            err = validate_cfg(cfg)
            if err:
                ui.messageBox(err)
                return

            _count, status, warnings = build_cabinet(design, cfg)
            notes = list(warnings)
            if status == 'none':
                notes.append('The panels could not be connected automatically. '
                             'They are still positioned correctly.')
            if notes:
                ui.messageBox('Cabinet created with notes:\n\n- ' + '\n- '.join(notes))
        except ValueError as e:
            ui.messageBox(str(e))
        except PartDesignNotSupportedError as e:
            if ui:
                ui.messageBox(str(e))
        except:
            if ui:
                ui.messageBox('New Cabinet failed:\n{}'.format(traceback.format_exc()))


# -----------------------------------------------------------------------------
# Edit Cabinet command: pick a stored cabinet, tweak any attribute, regenerate
# in place (delete + rebuild from the edited config).
# -----------------------------------------------------------------------------
class EditCabinetCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        global _edit_cabinets, _context_edit_token
        try:
            design = get_design()
            if not design:
                ui.messageBox('Open a Design document first.')
                return
            cabinets = collect_cabinets(design)
            if not cabinets:
                ui.messageBox('No FusionMob cabinets found in this document.\n'
                              'Create one with New Cabinet first.')
                return
            _edit_cabinets = cabinets

            # When launched from the right-click menu on a cabinet, pre-select
            # that cabinet instead of defaulting to the first one.
            preselect_idx = 0
            if _context_edit_token:
                for i, (occ, _cfg) in enumerate(cabinets):
                    try:
                        if occ.entityToken == _context_edit_token:
                            preselect_idx = i
                            break
                    except:
                        pass
                _context_edit_token = None

            inputs = args.command.commandInputs
            pick = inputs.addDropDownCommandInput(
                'cabinetPick', 'Cabinet', adsk.core.DropDownStyles.TextListDropDownStyle)
            for i, (occ, _cfg) in enumerate(cabinets):
                pick.listItems.add('{0}. {1}'.format(i + 1, occ.component.name), i == preselect_idx)

            add_cabinet_inputs(inputs, cabinets[preselect_idx][1])

            onChange = EditCabinetInputChangedHandler()
            args.command.inputChanged.add(onChange)
            handlers.append(onChange)
            onExec = EditCabinetExecuteHandler()
            args.command.execute.add(onExec)
            handlers.append(onExec)
        except:
            if ui:
                ui.messageBox('Edit Cabinet setup failed:\n{}'.format(traceback.format_exc()))


class EditCabinetInputChangedHandler(adsk.core.InputChangedEventHandler):
    def notify(self, args):
        try:
            cid = args.input.id
            if cid == 'advancedMode':
                _apply_cabinet_advanced_visibility(args.inputs, args.input.value)
                return
            if cid == 'arrTopMode':
                _apply_arremate_top_mode_visibility(args.inputs)
                return
            if cid in ('slideKey', 'slideCustom'):
                _apply_slide_custom_state(args.inputs, refresh_from_spec=(cid == 'slideKey'))
                return
            if cid != 'cabinetPick':
                return
            idx = args.input.selectedItem.index
            if 0 <= idx < len(_edit_cabinets):
                write_cabinet_inputs(args.inputs, _edit_cabinets[idx][1])
                _apply_arremate_top_mode_visibility(args.inputs)
        except:
            if ui:
                ui.messageBox('Edit Cabinet input failed:\n{}'.format(traceback.format_exc()))


class EditCabinetExecuteHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            design = get_design()
            if not design:
                return
            inputs = args.command.commandInputs
            idx = inputs.itemById('cabinetPick').selectedItem.index
            if not (0 <= idx < len(_edit_cabinets)):
                return
            occ, _old_cfg = _edit_cabinets[idx]

            # This dialog only exposes a single-region cabinet. If the cabinet
            # carries a custom multi-region layout, editing it here would flatten
            # that layout — defer to the visual Cabinet Layout palette instead.
            if is_layout_split(_old_cfg):
                ui.messageBox('This cabinet has a custom multi-region layout.\n\n'
                              'Edit it with the "Cabinet Layout" command so its '
                              'regions are preserved.')
                return

            cfg = read_cabinet_inputs(inputs)
            err = validate_cfg(cfg)
            if err:
                ui.messageBox(err)
                return

            # Carry forward state the dialog doesn't round-trip (parameter prefix,
            # per-panel fita overrides) and snapshot appearances before deleting.
            _carry_forward_cabinet_cfg(cfg, _old_cfg)
            state = capture_cabinet_state(occ)

            # Keep the cabinet in its current spot: reuse its position, then
            # delete the old assembly and rebuild from the edited config.
            try:
                v = occ.transform.translation
                translation = (v.x, v.y, v.z)
            except:
                translation = None
            before = _root_tokens(design)
            try:
                occ.deleteMe()
            except:
                pass

            _count, status, warnings = build_cabinet(design, cfg, translation)
            new_occ = _find_occ_by_token(design, _new_root_token(design, before))
            restore_cabinet_state(new_occ, state)
            notes = list(warnings)
            if status == 'none':
                notes.append('The panels could not be connected automatically. '
                             'They are still positioned correctly.')
            if notes:
                ui.messageBox('Cabinet updated with notes:\n\n- ' + '\n- '.join(notes))
        except ValueError as e:
            ui.messageBox(str(e))
        except PartDesignNotSupportedError as e:
            if ui:
                ui.messageBox(str(e))
        except:
            if ui:
                ui.messageBox('Edit Cabinet failed:\n{}'.format(traceback.format_exc()))


# -----------------------------------------------------------------------------
# Right-click (marking menu) integration: when a FusionMob cabinet is
# right-clicked in the browser or canvas, add an "Edit Cabinet" entry that opens
# the edit dialog pre-focused on that cabinet.
# -----------------------------------------------------------------------------
def _cabinet_occ_from_entity(ent):
    """Walk up from a selected entity to the FusionMob cabinet occurrence that
    contains it (the one carrying a stored config), or None."""
    if ent is None:
        return None
    occ = ent if isinstance(ent, adsk.fusion.Occurrence) else getattr(ent, 'assemblyContext', None)
    seen = 0
    while occ and seen < 50:
        try:
            attr = occ.component.attributes.itemByName(ATTR_GROUP, CABINET_CFG_ATTR)
            if attr and attr.value:
                return occ
        except:
            pass
        try:
            occ = occ.assemblyContext
        except:
            occ = None
        seen += 1
    return None


def _cabinet_from_collection(coll):
    """Scan a Fusion collection for the first FusionMob cabinet occurrence.

    Tolerant of API shape differences: a marking menu's `selectedEntities` holds
    entities directly, while `activeSelections` holds Selection wrappers (with an
    `.entity`). Every access is guarded so a right-click can never raise."""
    if not coll:
        return None
    try:
        n = coll.count
    except:
        return None
    for i in range(n):
        try:
            item = coll.item(i)
        except:
            continue
        ent = getattr(item, 'entity', item)  # unwrap Selection -> entity
        occ = _cabinet_occ_from_entity(ent)
        if occ:
            return occ
    return None


def _find_cabinet_occ(args):
    """The FusionMob cabinet under the cursor: try the marking menu's selection
    first, then fall back to the app's active selection."""
    try:
        occ = _cabinet_from_collection(args.selectedEntities)
    except:
        occ = None
    if occ:
        return occ
    try:
        return _cabinet_from_collection(ui.activeSelections)
    except:
        return None


# -----------------------------------------------------------------------------
# Edit Panel command — edit one body's edge banding (fita) in place.
#
# Fita is pure cut-list metadata (not modelled geometry), so editing a panel's
# banding is just a rewrite of its panelData attribute — no rebuild. Select one
# or more tagged bodies, set the four edges (Nenhuma / 0.4mm / 1mm), and the
# chosen tape name is written to fita_C1..L2. Available on the ribbon and from
# the right-click menu when a tagged body is selected.
# -----------------------------------------------------------------------------
def _panel_body_from_entity(ent):
    """Return `ent` if it is a BRep body carrying our panelData attribute, else
    None. Guarded so a right-click can never raise."""
    try:
        if not isinstance(ent, adsk.fusion.BRepBody):
            return None
        attr = ent.attributes.itemByName(ATTR_GROUP, ATTR_NAME)
        if attr and attr.value:
            return ent
    except:
        pass
    return None


def _panel_body_from_collection(coll):
    """First tagged panel body in a Fusion collection (marking-menu entities or
    activeSelections wrappers), or None."""
    if not coll:
        return None
    try:
        n = coll.count
    except:
        return None
    for i in range(n):
        try:
            item = coll.item(i)
        except:
            continue
        ent = getattr(item, 'entity', item)  # unwrap Selection -> entity
        body = _panel_body_from_entity(ent)
        if body:
            return body
    return None


def _read_panel_data(body):
    """The body's panelData dict, or None if absent/unparseable."""
    try:
        attr = body.attributes.itemByName(ATTR_GROUP, ATTR_NAME)
        if attr and attr.value:
            return json.loads(attr.value)
    except (ValueError, TypeError):
        pass
    return None


def _load_fita_dropdowns(inputs, body):
    """Seed the four edge dropdowns from a body's stored fita, classified against
    the tape-name fields currently in the dialog."""
    data = _read_panel_data(body) if body else None
    thin = inputs.itemById('epFitaThin').value
    thick = inputs.itemById('epFitaThick').value
    info = ''
    if data:
        info = '{0}  |  {1}  ({2:.0f} x {3:.0f} mm)'.format(
            data.get('complemento', ''), data.get('funcao', ''),
            data.get('comprimento_mm', 0), data.get('largura_mm', 0))
    for edge, key in (('epC1', 'fita_C1'), ('epC2', 'fita_C2'),
                      ('epL1', 'fita_L1'), ('epL2', 'fita_L2')):
        val = _fita_value_for(data.get(key, '') if data else '', thin, thick)
        _select_dropdown(inputs.itemById(edge), _fita_choice_label(val))
    inputs.itemById('epInfo').text = info or 'Selecione um painel FusionMob.'


class EditPanelCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        global _context_panel_token
        try:
            design = get_design()
            if not design:
                ui.messageBox('Open a Design document first.')
                return
            inputs = args.command.commandInputs

            sel = inputs.addSelectionInput('panelSel', 'Painel (body)',
                                           'Selecione um ou mais paineis FusionMob')
            sel.addSelectionFilter('SolidBodies')
            sel.setSelectionLimits(1, 0)

            inputs.addTextBoxCommandInput('epInfo', '', 'Selecione um painel FusionMob.', 2, True)

            # Tape names (default to the FITA defaults; the operator can override
            # the exact tape written).
            inputs.addStringValueInput('epFitaThin', 'Fita 0.4mm (nome)', FITA['name_thin'])
            inputs.addStringValueInput('epFitaThick', 'Fita 1mm (nome)', FITA['name_thick'])

            grp = inputs.addGroupCommandInput('epEdges', 'Fita por borda')
            grp.isExpanded = True
            g = grp.children
            for edge, label in (('epC1', 'Fita C1 (comprimento)'),
                                ('epC2', 'Fita C2 (comprimento)'),
                                ('epL1', 'Fita L1 (largura)'),
                                ('epL2', 'Fita L2 (largura)')):
                dd = g.addDropDownCommandInput(edge, label,
                                               adsk.core.DropDownStyles.TextListDropDownStyle)
                for (_v, lbl) in FITA_CHOICES:
                    dd.listItems.add(lbl, _v == 'none')

            # Pre-select the right-clicked body, if any.
            preset = None
            if _context_panel_token:
                try:
                    ents = design.findEntityByToken(_context_panel_token)
                    for e in (ents or []):
                        if _panel_body_from_entity(e):
                            preset = e
                            break
                except:
                    preset = None
                _context_panel_token = None
            if preset is None:
                preset = _panel_body_from_collection(ui.activeSelections)
            if preset is not None:
                try:
                    sel.addSelection(preset)
                except:
                    pass
            _load_fita_dropdowns(inputs, preset)

            onChange = EditPanelInputChangedHandler()
            args.command.inputChanged.add(onChange)
            handlers.append(onChange)
            onExec = EditPanelExecuteHandler()
            args.command.execute.add(onExec)
            handlers.append(onExec)
        except:
            if ui:
                ui.messageBox('Edit Panel setup failed:\n{}'.format(traceback.format_exc()))


class EditPanelInputChangedHandler(adsk.core.InputChangedEventHandler):
    def notify(self, args):
        try:
            # Reload the edge dropdowns when the selection or a tape name changes.
            if args.input.id not in ('panelSel', 'epFitaThin', 'epFitaThick'):
                return
            sel = args.inputs.itemById('panelSel')
            body = sel.selection(0).entity if sel and sel.selectionCount else None
            _load_fita_dropdowns(args.inputs, body)
        except:
            if ui:
                ui.messageBox('Edit Panel input failed:\n{}'.format(traceback.format_exc()))


class EditPanelExecuteHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            inputs = args.command.commandInputs
            sel = inputs.itemById('panelSel')
            thin = inputs.itemById('epFitaThin').value
            thick = inputs.itemById('epFitaThick').value
            names = {'none': '', 'thin': thin, 'thick': thick}
            chosen = {
                'fita_C1': names[_fita_choice_value(inputs.itemById('epC1').selectedItem.name)],
                'fita_C2': names[_fita_choice_value(inputs.itemById('epC2').selectedItem.name)],
                'fita_L1': names[_fita_choice_value(inputs.itemById('epL1').selectedItem.name)],
                'fita_L2': names[_fita_choice_value(inputs.itemById('epL2').selectedItem.name)],
            }
            updated = 0
            # Also record each override in its owning cabinet's stored cfg
            # (keyed by body name) so it survives a rebuild. Accumulate per
            # cabinet occurrence so we rewrite each cfg attribute only once.
            cab_overrides = {}   # cabinet occ -> {body_name: chosen}
            for i in range(sel.selectionCount):
                body = sel.selection(i).entity
                data = _read_panel_data(body)
                if data is None:
                    continue
                data.update(chosen)
                body.attributes.add(ATTR_GROUP, ATTR_NAME, json.dumps(data))
                updated += 1
                try:
                    cab = _cabinet_occ_from_entity(body)
                    if cab and body.name:
                        cab_overrides.setdefault(cab, {})[body.name] = dict(chosen)
                except Exception:
                    pass
            for cab, per_body in cab_overrides.items():
                try:
                    attr = cab.component.attributes.itemByName(ATTR_GROUP, CABINET_CFG_ATTR)
                    if not (attr and attr.value):
                        continue
                    cfg = normalize_cfg(json.loads(attr.value))
                    ov = cfg.setdefault('panel_overrides', {})
                    ov.update(per_body)
                    cab.component.attributes.add(ATTR_GROUP, CABINET_CFG_ATTR, json.dumps(cfg))
                except Exception:
                    pass
            if updated == 0:
                ui.messageBox('No FusionMob panels were updated (select tagged bodies).')
        except:
            if ui:
                ui.messageBox('Edit Panel failed:\n{}'.format(traceback.format_exc()))


class CabinetMarkingMenuHandler(adsk.core.MarkingMenuEventHandler):
    def notify(self, args):
        global _context_edit_token, _context_panel_token
        try:
            _context_edit_token = None
            _context_panel_token = None
            controls = args.linearMarkingMenu.controls

            # Edit Panel: offered when a tagged body is directly selected.
            body = _panel_body_from_collection(args.selectedEntities)
            if body is None:
                body = _panel_body_from_collection(ui.activeSelections)
            if body is not None:
                panel_def = ui.commandDefinitions.itemById(EDIT_PANEL_CMD_ID)
                if panel_def:
                    _context_panel_token = body.entityToken
                    try:
                        controls.addSeparator()
                    except:
                        pass
                    controls.addCommand(panel_def)

            occ = _find_cabinet_occ(args)
            if not occ:
                return
            cmd_def = ui.commandDefinitions.itemById(EDIT_CABINET_CMD_ID)
            if not cmd_def:
                return
            # Remember which cabinet was clicked so the dialog can pre-select it.
            _context_edit_token = occ.entityToken

            # Append "Edit Cabinet" to the end of the native context menu, after
            # a separator so it reads as our own addition.
            try:
                controls.addSeparator()
            except:
                pass
            controls.addCommand(cmd_def)
        except:
            # This handler fires on every right-click, so never surface errors
            # here — a failure just means no menu entry this time.
            pass


# -----------------------------------------------------------------------------
# Export Cut List command
# -----------------------------------------------------------------------------
class ExportCutListCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            execHandler = ExportCutListExecuteHandler()
            args.command.execute.add(execHandler)
            handlers.append(execHandler)
        except:
            if ui:
                ui.messageBox('Export setup failed:\n{}'.format(traceback.format_exc()))


class ExportCutListExecuteHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            design = get_design()
            if not design:
                ui.messageBox('Open a Design document first.')
                return

            rows = collect_panels(design)
            if not rows:
                ui.messageBox('No FusionMob panels found in this document.\n'
                              'Use "New Panel" to create some first.')
                return

            dlg = ui.createFileDialog()
            dlg.title = 'Export CorteCloud Cut List'
            dlg.filter = 'CSV files (*.csv)'
            dlg.initialFilename = 'cortecloud_importar.csv'
            if dlg.showSave() != adsk.core.DialogResults.DialogOK:
                return

            write_cutlist_csv(dlg.filename, rows)
            total_parts = sum(r.get('quantidade', 1) for r in rows)
            ui.messageBox('Exported {} panel type(s), {} part(s) total to:\n{}'.format(
                len(rows), total_parts, dlg.filename))
        except:
            if ui:
                ui.messageBox('Export failed:\n{}'.format(traceback.format_exc()))


# -----------------------------------------------------------------------------
# Cabinet Layout command + palette: a visual editor for the interior region grid.
# The palette (an HTML page) lets the user split the interior into regions, set
# each region's content (open/shelves/doors/drawers) and hit Apply to (re)build
# the whole cabinet — the same delete-and-rebuild flow Edit Cabinet uses, so the
# user never touches individual bodies. JS <-> Python talk over the palette's
# incomingFromHTML channel (JSON strings; the reply is set on returnData).
# -----------------------------------------------------------------------------
def _root_tokens(design):
    out = set()
    for o in design.rootComponent.occurrences:
        try:
            out.add(o.entityToken)
        except Exception:
            pass
    return out


def _new_root_token(design, before):
    for o in design.rootComponent.occurrences:
        try:
            if o.entityToken not in before:
                return o.entityToken
        except Exception:
            continue
    return None


def _find_occ_by_token(design, token):
    for o in design.rootComponent.occurrences:
        try:
            if o.entityToken == token:
                return o
        except Exception:
            continue
    return None


def _cabinet_list(design):
    """[{'id': token, 'name': ...}] for every stored cabinet, for the target list."""
    out = []
    for occ, _cfg in collect_cabinets(design):
        try:
            out.append({'id': occ.entityToken, 'name': occ.component.name})
        except Exception:
            pass
    return out


def _palette_state(design):
    """Initial payload for the editor: the option lists, the known cabinets and a
    fresh default config to start a 'new' cabinet from."""
    return {
        'cfg': effective_default_cfg(),
        'cabinets': _cabinet_list(design) if design else [],
        'materials': [name for name, _thk in get_materials()],
        'slides': slide_specs_for_ui(),
        # Same option list the Preferences palette gets: both render their
        # configuration form from resources/ui/cabinet_config.js.
        'fita_choices': [{'value': v, 'label': lbl} for v, lbl in FITA_CHOICES],
    }


def _palette_target(design, token):
    """The config for a chosen target ('new' or a cabinet token)."""
    if design and token and token != 'new':
        occ = _find_occ_by_token(design, token)
        if occ:
            attr = occ.component.attributes.itemByName(ATTR_GROUP, CABINET_CFG_ATTR)
            if attr and attr.value:
                try:
                    return {'cfg': normalize_cfg(json.loads(attr.value)), 'id': token}
                except (ValueError, TypeError):
                    pass
    return {'cfg': effective_default_cfg(), 'id': 'new'}


def _palette_apply(design, data):
    """Validate the edited config and (re)build the cabinet in place, returning the
    new cabinet's token + the refreshed cabinet list so the editor can re-select it."""
    cfg = normalize_cfg(data.get('cfg') or {})
    err = validate_cfg(cfg)
    if err:
        return {'ok': False, 'error': err}
    token = data.get('id')
    translation = None
    state = None
    if token and token != 'new':
        occ = _find_occ_by_token(design, token)
        if occ:
            # Carry forward parameter prefix + fita overrides from the stored cfg
            # (the palette's edited tree doesn't round-trip them) and snapshot
            # appearances before deleting.
            attr = occ.component.attributes.itemByName(ATTR_GROUP, CABINET_CFG_ATTR)
            if attr and attr.value:
                try:
                    _carry_forward_cabinet_cfg(cfg, normalize_cfg(json.loads(attr.value)))
                except (ValueError, TypeError):
                    pass
            state = capture_cabinet_state(occ)
            try:
                v = occ.transform.translation
                translation = (v.x, v.y, v.z)
            except Exception:
                translation = None
            try:
                occ.deleteMe()
            except Exception:
                pass
    before = _root_tokens(design)
    _count, status, warnings = build_cabinet(design, cfg, translation)
    new_token = _new_root_token(design, before)
    if state:
        restore_cabinet_state(_find_occ_by_token(design, new_token), state)
    return {'ok': True, 'status': status, 'warnings': list(warnings),
            'id': new_token, 'cabinets': _cabinet_list(design)}


class LayoutPaletteHTMLHandler(adsk.core.HTMLEventHandler):
    def notify(self, args):
        try:
            action = args.action
            data = json.loads(args.data) if args.data else {}
            design = get_design()
            if action == 'init':
                args.returnData = json.dumps(_palette_state(design))
            elif action == 'selectTarget':
                args.returnData = json.dumps(_palette_target(design, data.get('id')))
            elif action == 'validate':
                err = validate_cfg(data.get('cfg') or {})
                args.returnData = json.dumps({'ok': err is None, 'error': err})
            elif action == 'apply':
                if not design:
                    args.returnData = json.dumps({'ok': False, 'error': 'Open a Design document first.'})
                else:
                    args.returnData = json.dumps(_palette_apply(design, data))
            else:
                args.returnData = json.dumps({'ok': False, 'error': 'Unknown action.'})
        except PartDesignNotSupportedError as e:
            args.returnData = json.dumps({'ok': False, 'error': str(e)})
        except Exception:
            try:
                args.returnData = json.dumps({'ok': False, 'error': traceback.format_exc()})
            except Exception:
                pass


def _show_layout_palette():
    """Create the layout palette on first use, then reveal it."""
    global _layout_palette_handler
    palettes = ui.palettes
    pal = palettes.itemById(LAYOUT_PALETTE_ID)
    if not pal:
        # Fusion turns this into a file:// URL; Windows backslashes get mangled
        # into %5C ("ERR_INVALID_URL"), so hand it a forward-slash path.
        html_path = os.path.join(RES_DIR, 'ui', 'layout_editor.html').replace('\\', '/')
        pal = palettes.add(LAYOUT_PALETTE_ID, 'FusionMob - Layout', html_path,
                           True, True, True, 480, 680)
        try:
            pal.dockingState = adsk.core.PaletteDockingStates.PaletteDockStateRight
        except Exception:
            pass
        if _layout_palette_handler is None:
            _layout_palette_handler = LayoutPaletteHTMLHandler()
        pal.incomingFromHTML.add(_layout_palette_handler)
        handlers.append(_layout_palette_handler)
    pal.isVisible = True


class CabinetLayoutCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            _show_layout_palette()
        except Exception:
            if ui:
                ui.messageBox('Cabinet Layout setup failed:\n{}'.format(traceback.format_exc()))


# -----------------------------------------------------------------------------
# Preferences palette (edit the persistent material library + cabinet defaults)
# -----------------------------------------------------------------------------
def _prefs_state():
    """Payload for the preferences editor: the current (saved-or-factory) material
    library and cabinet defaults of the ACTIVE profile, the profile list, the option
    lists, plus the pristine factory values so the editor's Reset can restore them
    without another round-trip."""
    def mats(pairs):
        return [{'name': n, 'thickness': t} for n, t in pairs]
    store = load_preferences()
    return {
        # With nothing saved yet the active profile exists only conceptually
        # (factory-valued); still list it so the picker is never empty.
        'profiles': profile_names() or [store['active_profile']],
        'active': store['active_profile'],
        'materials': mats(get_materials()),
        'cfg': effective_default_cfg(),
        'slides': slide_specs_for_ui(),
        'fita_choices': [{'value': v, 'label': lbl} for v, lbl in FITA_CHOICES],
        'factory': {
            'materials': mats(MATERIALS),
            'cfg': normalize_cfg(DEFAULT_CFG),
        },
    }


def _clean_materials(raw):
    """Validate a materials payload into [{'name','thickness'}]. Returns
    (materials, error): names non-empty and unique (case-insensitive), thickness a
    non-negative number; at least one material required."""
    if not isinstance(raw, list) or not raw:
        return None, 'Adicione ao menos um material (chapa).'
    out, seen = [], set()
    for m in raw:
        name = str((m.get('name') if isinstance(m, dict) else '') or '').strip()
        if not name:
            return None, 'Todo material precisa de um nome.'
        key = name.lower()
        if key in seen:
            return None, 'Material duplicado: "{}".'.format(name)
        seen.add(key)
        try:
            thk = float(m.get('thickness') or 0.0)
        except (TypeError, ValueError):
            return None, 'Espessura inválida para "{}".'.format(name)
        if thk < 0:
            return None, 'Espessura inválida para "{}".'.format(name)
        out.append({'name': name, 'thickness': thk})
    return out, None


def _store_copy():
    """A private copy of the cached store, safe to mutate: a failed/cancelled
    operation must never leave the in-memory cache out of sync with the file."""
    return json.loads(json.dumps(load_preferences()))


def _factory_materials():
    return [{'name': n, 'thickness': t} for n, t in MATERIALS]


def _profile_in(store, name):
    for p in store['profiles']:
        if p['name'] == name:
            return p
    return None


def _apply_edits_to_active(store, data):
    """Validate the editor's materials + cabinet defaults and write them into
    `store`'s active profile (creating that profile if it isn't stored yet).
    Returns an error string, or None on success (store mutated in place)."""
    materials, err = _clean_materials(data.get('materials'))
    if err:
        return err
    defaults = data.get('cabinet_defaults')
    if not isinstance(defaults, dict):
        defaults = {}
    # Validate the defaults as a real cabinet config (normalize first, as apply does).
    err = validate_cfg(normalize_cfg(defaults))
    if err:
        return err
    # Store flat defaults only — never pin an interior layout, so New Cabinet keeps
    # synthesizing a fresh single region from the flat fields.
    stored = dict(defaults)
    stored.pop('layout', None)
    prof = _profile_in(store, store['active_profile'])
    if prof is None:
        prof = {'name': store['active_profile'], 'materials': [], 'cabinet_defaults': {}}
        store['profiles'].append(prof)
    prof['materials'] = materials
    prof['cabinet_defaults'] = stored
    return None


def _prefs_result(note=None, **extra):
    """A refreshed state payload for the editor, plus ok/note and any extras."""
    state = _prefs_state()
    state['ok'] = True
    if note:
        state['note'] = note
    state.update(extra)
    return state


def _prefs_save(data):
    """Validate and persist the edited preferences into the active profile."""
    store = _store_copy()
    err = _apply_edits_to_active(store, data)
    if err:
        return {'ok': False, 'error': err}
    try:
        save_preferences(store)
    except Exception as e:
        return {'ok': False, 'error': 'Não foi possível salvar as preferências: {}'.format(e)}
    return _prefs_result(path=prefs_path())


def _prefs_reset():
    """Restore factory values in the ACTIVE profile (other profiles are untouched)
    and return the fresh state."""
    store = _store_copy()
    prof = _profile_in(store, store['active_profile'])
    if prof is None:
        clear_preferences()
        return _prefs_result()
    prof['materials'] = _factory_materials()
    prof['cabinet_defaults'] = {}
    try:
        save_preferences(store)
    except Exception as e:
        return {'ok': False, 'error': 'Não foi possível salvar as preferências: {}'.format(e)}
    return _prefs_result()


def _stage_edits(store, data):
    """Best-effort: fold the editor's pending edits into `store`'s active profile
    before a profile/export operation, so what the user sees is what is carried
    over. Invalid edits are dropped (the store is left untouched) and reported as
    a note rather than blocking the operation. Returns (store, note)."""
    if data.get('materials') is None and data.get('cabinet_defaults') is None:
        return store, None
    staged = json.loads(json.dumps(store))
    err = _apply_edits_to_active(staged, data)
    if err:
        return store, 'Alterações não salvas foram descartadas: {}'.format(err)
    return staged, None


def _prefs_profile_op(action, data):
    """Profile management: select / create (blank or duplicate) / rename / delete.
    Pending editor changes are saved into the current profile first, so switching
    never silently loses work."""
    store, note = _stage_edits(_store_copy(), data)
    names = set(p['name'].lower() for p in store['profiles'])
    name = str(data.get('name') or '').strip()

    if action == 'profileSelect':
        if name and not _profile_in(store, name):
            return {'ok': False, 'error': 'Perfil não encontrado: "{}".'.format(name)}
        store['active_profile'] = name or store['active_profile']

    elif action == 'profileCreate':
        if not name:
            return {'ok': False, 'error': 'Informe um nome para o perfil.'}
        if name.lower() in names:
            return {'ok': False, 'error': 'Já existe um perfil chamado "{}".'.format(name)}
        if data.get('copy_current'):
            src = _profile_in(store, store['active_profile'])
            src = src or {'materials': _factory_materials(), 'cabinet_defaults': {}}
            prof = {'name': name,
                    'materials': [dict(m) for m in src['materials']],
                    'cabinet_defaults': dict(src['cabinet_defaults'])}
        else:
            prof = {'name': name, 'materials': _factory_materials(), 'cabinet_defaults': {}}
        store['profiles'].append(prof)
        store['active_profile'] = name

    elif action == 'profileRename':
        if not name:
            return {'ok': False, 'error': 'Informe um nome para o perfil.'}
        old = store['active_profile']
        if name != old and name.lower() in names:
            return {'ok': False, 'error': 'Já existe um perfil chamado "{}".'.format(name)}
        prof = _profile_in(store, old)
        if prof is None:   # nothing saved yet — the rename just names the first save
            store['profiles'].append({'name': name, 'materials': _factory_materials(),
                                      'cabinet_defaults': {}})
        else:
            prof['name'] = name
        store['active_profile'] = name

    elif action == 'profileDelete':
        if len(store['profiles']) <= 1:
            return {'ok': False, 'error': 'Mantenha ao menos um perfil. Use '
                                          '"Restaurar padrões de fábrica" para zerá-lo.'}
        target = name or store['active_profile']
        store['profiles'] = [p for p in store['profiles'] if p['name'] != target]
        store['active_profile'] = store['profiles'][0]['name']

    else:
        return {'ok': False, 'error': 'Unknown action.'}

    try:
        save_preferences(store)
    except Exception as e:
        return {'ok': False, 'error': 'Não foi possível salvar as preferências: {}'.format(e)}
    return _prefs_result(note)


def _prefs_filename(base):
    """A safe .json filename for the export dialog."""
    keep = [c if (c.isalnum() or c in ' -_') else '_' for c in base]
    slug = ''.join(keep).strip().replace(' ', '_') or 'perfil'
    return 'fusionmob_{}.json'.format(slug)


def _prefs_export(data):
    """Write the active profile (or every profile) to a .json file the user picks.
    Exports what the editor currently shows, without saving it to the store."""
    store, note = _stage_edits(_store_copy(), data)
    if data.get('scope') == 'all':
        profiles = store['profiles'] or [{'name': store['active_profile'],
                                          'materials': _factory_materials(),
                                          'cabinet_defaults': {}}]
        suggested = 'perfis'
    else:
        prof = _profile_in(store, store['active_profile'])
        profiles = [prof or {'name': store['active_profile'],
                             'materials': _factory_materials(),
                             'cabinet_defaults': {}}]
        suggested = profiles[0]['name']
    doc = {'kind': PREFS_FILE_KIND, 'version': PREFS_VERSION, 'app_version': __version__,
           'active_profile': profiles[0]['name'], 'profiles': profiles}
    dlg = ui.createFileDialog()
    dlg.title = 'FusionMob - exportar configuração'
    dlg.filter = 'FusionMob preferences (*.json)'
    dlg.initialFilename = _prefs_filename(suggested)
    if dlg.showSave() != adsk.core.DialogResults.DialogOK:
        return {'ok': False, 'cancelled': True}
    try:
        with open(dlg.filename, 'w', encoding='utf-8') as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return {'ok': False, 'error': 'Não foi possível exportar: {}'.format(e)}
    return _prefs_result(note, path=dlg.filename, count=len(profiles))


def _prefs_import(data):
    """Read a .json exported by this add-in (or a legacy preferences.json) and add
    its profiles to the store, renaming collisions instead of overwriting (import
    never destroys a saved profile). The first imported profile becomes active."""
    dlg = ui.createFileDialog()
    dlg.title = 'FusionMob - importar configuração'
    dlg.filter = 'FusionMob preferences (*.json)'
    dlg.isMultiSelectEnabled = False
    if dlg.showOpen() != adsk.core.DialogResults.DialogOK:
        return {'ok': False, 'cancelled': True}
    path = dlg.filename
    try:
        with open(path, 'r', encoding='utf-8') as f:
            doc = json.load(f)
    except Exception as e:
        return {'ok': False, 'error': 'Não foi possível ler o arquivo: {}'.format(e)}
    stem = os.path.splitext(os.path.basename(path))[0]
    incoming = migrate_prefs(doc, default_name=stem or DEFAULT_PROFILE_NAME)
    if not incoming['profiles']:
        return {'ok': False, 'error': 'O arquivo não contém nenhum perfil de configuração.'}

    store, note = _stage_edits(_store_copy(), data)
    added = []
    for p in incoming['profiles']:
        taken = set(q['name'].lower() for q in store['profiles'])
        p = dict(p)
        p['name'] = _unique_name(p['name'], taken)
        store['profiles'].append(p)
        added.append(p['name'])
    store['active_profile'] = added[0]
    try:
        save_preferences(store)
    except Exception as e:
        return {'ok': False, 'error': 'Não foi possível salvar as preferências: {}'.format(e)}
    msg = 'Importado(s) {} perfil(is): {}.'.format(len(added), ', '.join(added))
    return _prefs_result('{} {}'.format(note, msg) if note else msg, path=path, count=len(added))


class PrefsPaletteHTMLHandler(adsk.core.HTMLEventHandler):
    def notify(self, args):
        try:
            action = args.action
            data = json.loads(args.data) if args.data else {}
            if action == 'init':
                args.returnData = json.dumps(_prefs_state())
            elif action == 'save':
                args.returnData = json.dumps(_prefs_save(data))
            elif action == 'reset':
                args.returnData = json.dumps(_prefs_reset())
            elif action in ('profileSelect', 'profileCreate', 'profileRename', 'profileDelete'):
                args.returnData = json.dumps(_prefs_profile_op(action, data))
            elif action == 'export':
                args.returnData = json.dumps(_prefs_export(data))
            elif action == 'import':
                args.returnData = json.dumps(_prefs_import(data))
            else:
                args.returnData = json.dumps({'ok': False, 'error': 'Unknown action.'})
        except Exception:
            try:
                args.returnData = json.dumps({'ok': False, 'error': traceback.format_exc()})
            except Exception:
                pass


def _show_prefs_palette():
    """Create the preferences palette on first use, then reveal it."""
    global _prefs_palette_handler
    palettes = ui.palettes
    pal = palettes.itemById(PREFS_PALETTE_ID)
    if not pal:
        # Forward-slash path so Fusion's file:// URL isn't mangled (see layout palette).
        html_path = os.path.join(RES_DIR, 'ui', 'preferences.html').replace('\\', '/')
        pal = palettes.add(PREFS_PALETTE_ID, 'FusionMob - Preferencias', html_path,
                           True, True, True, 480, 680)
        try:
            pal.dockingState = adsk.core.PaletteDockingStates.PaletteDockStateRight
        except Exception:
            pass
        if _prefs_palette_handler is None:
            _prefs_palette_handler = PrefsPaletteHTMLHandler()
        pal.incomingFromHTML.add(_prefs_palette_handler)
        handlers.append(_prefs_palette_handler)
    pal.isVisible = True


class PreferencesCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            _show_prefs_palette()
        except Exception:
            if ui:
                ui.messageBox('Preferences setup failed:\n{}'.format(traceback.format_exc()))


# -----------------------------------------------------------------------------
# Add-in lifecycle
# -----------------------------------------------------------------------------
def _add_command(panel, cmd_id, name, desc, created_handler, icon_name, promoted=False):
    resource_folder = res(icon_name)
    cmd_def = ui.commandDefinitions.itemById(cmd_id)
    if not cmd_def:
        if resource_folder:
            cmd_def = ui.commandDefinitions.addButtonDefinition(cmd_id, name, desc, resource_folder)
        else:
            cmd_def = ui.commandDefinitions.addButtonDefinition(cmd_id, name, desc)
    elif resource_folder:
        # A definition left over from a previous session keeps its old icon;
        # re-point it so icon changes take effect without restarting Fusion.
        cmd_def.resourceFolder = resource_folder
    cmd_def.commandCreated.add(created_handler)
    handlers.append(created_handler)
    control = panel.controls.addCommand(cmd_def)
    if promoted:
        control.isPromotedByDefault = True
        control.isPromoted = True


def run(context):
    global app, ui
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface

        workspace = ui.workspaces.itemById(WORKSPACE_ID)

        tab = workspace.toolbarTabs.itemById(TAB_ID)
        if not tab:
            tab = workspace.toolbarTabs.add(TAB_ID, TAB_NAME)

        panel = tab.toolbarPanels.itemById(PANEL_ID)
        if not panel:
            panel = tab.toolbarPanels.add(PANEL_ID, 'Cabinet')

        _add_command(panel, NEW_PANEL_CMD_ID, 'New Panel',
                     'Create a parametric panel with edge banding',
                     NewPanelCreatedHandler(), 'new_panel')
        _add_command(panel, EDIT_PANEL_CMD_ID, 'Edit Panel',
                     'Edit a panel\'s edge banding (fita) in place',
                     EditPanelCreatedHandler(), 'edit_panel')
        # New Cabinet is created via the Cabinet Layout HTML palette (no ribbon command).
        _add_command(panel, EDIT_CABINET_CMD_ID, 'Edit Cabinet',
                     'Edit a cabinet and regenerate it',
                     EditCabinetCreatedHandler(), 'edit_cabinet', promoted=True)
        _add_command(panel, LAYOUT_CMD_ID, 'Cabinet Layout',
                     'Visually divide the cabinet interior into regions',
                     CabinetLayoutCreatedHandler(), 'cabinet_layout', promoted=True)
        _add_command(panel, EXPORT_CMD_ID, 'Export Cut List',
                     'Export all panels as a CorteCloud CSV',
                     ExportCutListCreatedHandler(), 'export', promoted=True)
        _add_command(panel, PREFS_CMD_ID, 'Preferences',
                     'Edit the material library and default cabinet parameters',
                     PreferencesCreatedHandler(), 'preferences', promoted=True)

        # Add "Edit Cabinet" to the right-click menu when a cabinet is clicked.
        global _marking_menu_handler
        _marking_menu_handler = CabinetMarkingMenuHandler()
        ui.markingMenuDisplaying.add(_marking_menu_handler)
        handlers.append(_marking_menu_handler)
    except:
        if ui:
            ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))


def stop(context):
    global _marking_menu_handler, _layout_palette_handler, _prefs_palette_handler
    try:
        if _marking_menu_handler:
            try:
                ui.markingMenuDisplaying.remove(_marking_menu_handler)
            except:
                pass
            _marking_menu_handler = None

        # Tear down the layout + preferences palettes.
        for pal_id in (LAYOUT_PALETTE_ID, PREFS_PALETTE_ID):
            pal = ui.palettes.itemById(pal_id)
            if pal:
                try:
                    pal.deleteMe()
                except:
                    pass
        _layout_palette_handler = None
        _prefs_palette_handler = None

        workspace = ui.workspaces.itemById(WORKSPACE_ID)
        tab = workspace.toolbarTabs.itemById(TAB_ID)
        if tab:
            panel = tab.toolbarPanels.itemById(PANEL_ID)
            if panel:
                for cmd_id in (NEW_PANEL_CMD_ID, EDIT_PANEL_CMD_ID,
                               EDIT_CABINET_CMD_ID, LAYOUT_CMD_ID, EXPORT_CMD_ID, PREFS_CMD_ID):
                    ctrl = panel.controls.itemById(cmd_id)
                    if ctrl:
                        ctrl.deleteMe()
                panel.deleteMe()
            tab.deleteMe()

        for cmd_id in (NEW_PANEL_CMD_ID, EDIT_PANEL_CMD_ID, EDIT_CABINET_CMD_ID,
                       LAYOUT_CMD_ID, EXPORT_CMD_ID, PREFS_CMD_ID):
            cmd_def = ui.commandDefinitions.itemById(cmd_id)
            if cmd_def:
                cmd_def.deleteMe()
    except:
        if ui:
            ui.messageBox('Stop failed:\n{}'.format(traceback.format_exc()))
