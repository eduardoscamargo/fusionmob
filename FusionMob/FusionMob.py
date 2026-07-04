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
__version__ = '1.2.2'

app = None
ui = None
handlers = []

# The right-clicked cabinet's entity token, set by the marking-menu handler so
# the Edit Cabinet dialog can pre-select it. Cleared once consumed.
_context_edit_token = None
# Reference to the marking-menu handler so stop() can unregister it.
_marking_menu_handler = None

WORKSPACE_ID = 'FusionSolidEnvironment'
TAB_ID = 'FusionMobTab'
TAB_NAME = 'FusionMob'
PANEL_ID = 'FusionMobPanel'

NEW_PANEL_CMD_ID = 'FusionMobNewPanel'
NEW_CABINET_CMD_ID = 'FusionMobNewCabinet'
EDIT_CABINET_CMD_ID = 'FusionMobEditCabinet'
LAYOUT_CMD_ID = 'FusionMobCabinetLayout'
EXPORT_CMD_ID = 'FusionMobExportCutList'

# Interior-layout editor palette (HTML). Reference kept so stop() can unregister.
LAYOUT_PALETTE_ID = 'FusionMobLayoutPalette'
_layout_palette_handler = None

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
    'with_back': True, 'back_material': 'MDF 6mm Cru',
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
    # clearances come from that spec (not stored here), so switching slides
    # re-sizes the boxes automatically. A lightweight proxy always represents the
    # slide; 'insert_real_hardware' additionally imports the bundled CAD model.
    'with_drawers': False, 'n_drawers': 3, 'drawer_inset': False, 'drawer_gap': 3.0,
    'slide_key': 'hafele_matrix_invisa_a30_300', 'insert_real_hardware': False,
    'drawer': dict(DRAWER),
    'tol': dict(DEFAULT_TOL),
    # Interior LAYOUT. None is a sentinel meaning "derive a single-region layout
    # from the flat fields above" (see normalize_cfg / _synthesize_layout_from_flat)
    # so old stored cabinets and the classic New/Edit dialog keep working. The
    # Cabinet Layout palette writes an explicit recursive region tree here: a node
    # is either a SPLIT {'split':'v'|'h','children':[{size,fixed,node},...]} or a
    # LEAF {'type':'open'|'shelves'|'doors'|'drawers', count, inset, ...}. When a
    # layout is present it is authoritative for the build. See build_region.
    'layout': None,
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
# NOTE: the seeded clearances (side/bottom/back, min depth) are best-guess values
# for the Häfele Matrix Invisa A30; confirm against the datasheet. They are pure
# data — editing hardware.json is enough, no code change.
# -----------------------------------------------------------------------------
HARDWARE_DIR = os.path.join(RES_DIR, 'hardware')

# Used when hardware.json is missing or a key can't be found, so drawer creation
# never breaks on a packaging/typo issue. Kept in sync with the seeded manifest.
FALLBACK_SLIDE = {
    'key': 'hafele_matrix_invisa_a30_300',
    'description': 'Corredica oculta Hafele Matrix Invisa A30 GT2 300mm (ext. total, push)',
    'type': 'undermount',
    'nominal_length_mm': 300.0,   # NL
    # Undermount planning rule (Hafele): the drawer bottom BETWEEN the sides =
    # clear carcass width - carcass_deduction; the outer box width follows from
    # the side thickness. So the drawer is sized off this deduction, not a raw
    # side gap. (Datasheet: max drawer width = clear width - 42 + 2*side_t.)
    'carcass_deduction': 42.0,
    'side_panel_thickness': 16.0, # thickness the runner is designed for (info)
    'side_clearance': 6.0,        # resulting per-side air gap with 15mm sides (info)
    'bottom_clearance': 13.0,     # gap under the box for the undermount mechanism
    'back_clearance': 18.0,       # gap box back <-> carcass back (rear coupling room)
    'min_cabinet_depth': 318.0,   # required internal depth for the 300mm NL
    'recommended_box_depth': 300.0,  # box side length ~ NL
    'base_depth_offset': 29.0,    # base panel depth = NL - 29 (info)
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
    FALLBACK_SLIDE. Never raises — an unknown key falls back to the manifest
    default, then to FALLBACK_SLIDE."""
    man = load_hardware_manifest()
    slides = man.get('slides', {}) if isinstance(man, dict) else {}
    key = cfg.get('slide_key') or man.get('default') or FALLBACK_SLIDE['key']
    spec = dict(FALLBACK_SLIDE)
    if isinstance(slides.get(key), dict):
        spec.update(slides[key])
    spec['key'] = key
    return spec


def slide_keys():
    """Ordered [(key, description)] for the dropdown; always non-empty."""
    man = load_hardware_manifest()
    slides = man.get('slides', {}) if isinstance(man, dict) else {}
    items = [(k, v.get('description', k)) for k, v in slides.items()]
    return items or [(FALLBACK_SLIDE['key'], FALLBACK_SLIDE['description'])]


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
    real.attributes.add(ATTR_GROUP, ATTR_NAME, json.dumps(data))
    return occ


def make_panel_data(funcao, complemento, dim_a_mm, dim_b_mm, material, girar='Sim', band=None):
    """Build a CorteCloud cut-list record for one carcass panel.

    Comprimento is the larger face dimension, Largura the smaller. When `band`
    (a tape name) is given, all four edges are banded with it — the usual case
    for doors, which are taped all around."""
    comp_mm = max(dim_a_mm, dim_b_mm)
    larg_mm = min(dim_a_mm, dim_b_mm)
    fita = band or ''
    return {
        'quantidade': 1,
        'comprimento_mm': round(comp_mm, 1),
        'largura_mm': round(larg_mm, 1),
        'funcao': funcao,
        'fita_C1': fita, 'fita_C2': fita, 'fita_L1': fita, 'fita_L2': fita,
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
            for i, (name, _thk) in enumerate(MATERIALS):
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
    0 (closed). Both doors' hinge edges run +Z, so a positive rotation swings
    them the same rotational sense: that opens a right-hinged door outward but a
    left-hinged one inward. Restricting the left door to the negative range
    (-110..0) makes it open outward instead. Optional; never fatal."""
    try:
        limits = joint.jointMotion.rotationLimits
        swing = math.radians(110.0)
        if hinge_side == 'left':
            lo, hi = -swing, 0.0
        else:
            lo, hi = 0.0, swing
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
    Best-effort — a door that can't be jointed is left positioned but free.
    Returns how many pivots were created."""
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
            ji = as_built.createInput(anchor_occ, occ, geo)
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


def _set_drawer_travel_limits(joint, travel_c):
    """Limit the slide to [0, travel_c] (cm): closed at rest (0), pulling out
    toward the FRONT up to ~full extension. The box-side edge used as the slide
    axis is oriented so a positive value moves the drawer forward (−Y), so the
    allowed range is the positive side. Optional; never fatal."""
    try:
        limits = joint.jointMotion.slideLimits
        limits.isMinimumValueEnabled = True
        limits.minimumValue = 0.0
        limits.isMaximumValueEnabled = True
        limits.maximumValue = abs(travel_c)
    except:
        pass


def attach_drawer_slides(cabinet_comp, anchor_occ, drawer_units):
    """Give each drawer (Gaveta) component a horizontal slider (prismatic) joint to
    the carcass so it pulls open along the cabinet depth (Y). Each unit is a dict
    {'occ', 'edge_x_c', 'edge_z_c', 'travel_c'}; the joint axis is a Y-running edge
    of the drawer's own box-side body (at edge_x_c/edge_z_c). As-built joints need
    real geometry for non-rigid motion (None only works for rigid joints), which is
    why we locate that edge. Best-effort — a drawer that can't be jointed is left
    positioned but free. Returns how many slides were created."""
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
            ji = as_built.createInput(anchor_occ, occ, geo)
            ji.setAsSliderJointMotion(
                adsk.fusion.JointDirections.CustomJointDirection, edge)
            joint = as_built.add(ji)
            _set_drawer_travel_limits(joint, unit['travel_c'])
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
    """Slide spec for a drawers leaf, honouring a per-leaf slide_key override."""
    return resolve_slide_spec({'slide_key': node.get('slide_key') or ctx.slide_key})


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
                      make_panel_data('Prateleira', name, width_c * 10.0, shelf_depth, ctx.material))


def build_doors(band, node, ctx, prefix):
    """Frameless doors filling the band (overlay or inset), with concealed-hinge
    cup bores and — for the outermost doors, whose hinge sits at a real vertical
    panel — mounting-plate pilots registered onto that panel via ctx.hole_map."""
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

    door_h_mm = region_h_mm - 2 * gap
    door_w_mm = (region_w_mm - (n + 1) * gap) / n
    door_w_c = door_w_mm / 10.0
    door_h_c = door_h_mm / 10.0
    band_mid = (band.x0 + band.x1) / 2.0

    hinge_zs = []
    if with_hinges:
        hinge_zs = hinge_z_positions(door_h_mm, door_z0, hinge['end_inset'])
        sb = int(node.get('shelves_behind', 0) or 0)
        shelf_bottoms = band_shelf_z_bottoms(band.z0, band.z1, ctx.t, sb) if sb > 0 else []
        if shelf_bottoms:
            cup_rad_c = (hinge['cup_diameter'] / 2.0) / 10.0
            lo = door_z0 + cup_rad_c + 0.5
            hi = door_z0 + door_h_mm / 10.0 - cup_rad_c - 0.5
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
    for i in range(n):
        x0 = span0_c + gap_c + i * (door_w_c + gap_c)
        ctx.door_i += 1
        name = _pname(ctx, prefix, 'Porta', ctx.door_i, single_ok=(ctx.single_leaf and n == 1))
        data = make_panel_data('Porta', name, door_h_mm, door_w_mm,
                               ctx.door_material, girar='Nao', band=ctx.door_band)
        door_center = x0 + door_w_c / 2.0
        if door_center <= band_mid:
            hinge_x_c, hinge_side = x0, 'left'
        else:
            hinge_x_c, hinge_side = x0 + door_w_c, 'right'
        cup_holes = None
        if with_hinges:
            cup_x = x0 + edge_c if hinge_side == 'left' else x0 + door_w_c - edge_c
            cup_holes = [(cup_x, door_back_c + eps, z, cup_x, door_back_c - cup_d, z, cup_r)
                         for z in hinge_zs]
            data['complemento'] = '{0} ({1}x dobradica caneco {2:.0f}mm)'.format(
                name, len(hinge_zs), hinge['cup_diameter'])
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
    from the band's clear width so slides fit whatever bounds the region."""
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

    region_inner_w_mm = (band.x1 - band.x0) * 10.0
    deduction_mm = spec.get('carcass_deduction')
    if deduction_mm is None:
        deduction_mm = 2.0 * spec.get('side_clearance', 0.0)
    box_outer_w_mm = region_inner_w_mm - deduction_mm + 2 * box_t
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
    pW_c = spec['proxy_W'] / 10.0
    slide_len_c = min(spec['proxy_L'] / 10.0, box_depth_c)

    for i in range(n):
        ctx.drawer_i += 1
        label = _pname(ctx, prefix, 'Gaveta', ctx.drawer_i)
        fz0 = z_region0 + dg_c + i * (face_h_c + dg_c)
        ftop = fz0 + face_h_c
        bz0 = max(fz0, base_top_c) + bc_c
        runner_z0 = bz0 - bc_c
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

        face_data = make_panel_data('Porta', label + ' Frente', face_h_mm, face_w_mm,
                                    drawer['face_material'], girar='Nao', band=drawer['face_band'])
        face_data['complemento'] = '{0} Frente (corredica {1}, par {2:.0f}mm)'.format(
            label, spec['description'], spec['nominal_length_mm'])
        add_solid_body(dcomp, label + ' Frente',
            (face_x0_c, face_y0_c, fz0, face_w_c, face_t_c, face_h_c), face_data)

        for side, sx0 in (('E', box_x0_c), ('D', box_x0_c + box_outer_w_c - pW_c)):
            nm = '{0} Corredica {1}'.format(label, side)
            if ctx.hw_comp is not None:
                ctx.hw_slots.append((nm, (sx0, box_y0_c, runner_z0)))
            else:
                add_solid_body(dcomp, nm, (sx0, box_y0_c, runner_z0, pW_c, slide_len_c, bc_c))

        ctx.drawer_bundles.append({'occ': drawer_occ, 'edge_x_c': box_x0_c,
                                   'edge_z_c': bz0, 'travel_c': box_depth_c * 0.9})


def build_region_leaf(band, node, ctx, prefix):
    typ = node.get('type', 'open')
    if typ == 'shelves':
        build_shelves(band, node, ctx, prefix)
    elif typ == 'doors':
        build_doors(band, node, ctx, prefix)
    elif typ == 'drawers':
        build_drawers(band, node, ctx, prefix)
    # 'open' -> nothing


def build_cabinet(design, cfg, translation=None):
    """Build the carcass as one assembly of per-panel components from a config
    dict (all lengths in mm). Stores the config on the cabinet component so it
    can be edited later. `translation` (cm tuple) pins the position on rebuild.

    Returns (part_count, assembly_status, warnings) where warnings is a list of
    non-fatal notes (e.g. a hinge that couldn't be moved clear of a shelf)."""
    cfg = normalize_cfg(cfg)   # fill defaults + synthesize/normalize the layout
    W, H, D, t = cfg['W'], cfg['H'], cfg['D'], cfg['t']
    n_shelves, material = cfg['n_shelves'], cfg['material']
    shelf_align_front = cfg.get('shelf_align_front', False)
    with_back, back_material = cfg['with_back'], cfg['back_material']
    back_t, dado_depth, back_setback = cfg['back_t'], cfg['dado_depth'], cfg['back_setback']
    with_toe_kick = cfg['with_toe_kick']
    toe_kick_material, toe_kick_t = cfg['toe_kick_material'], cfg['toe_kick_t']
    toe_kick_height, toe_kick_setback = cfg['toe_kick_height'], cfg['toe_kick_setback']
    toe_kick_max_span = cfg['toe_kick_max_span']
    with_doors, door_material = cfg['with_doors'], cfg['door_material']
    door_t, n_doors = cfg['door_t'], cfg['n_doors']
    door_gap, door_band = cfg['door_gap'], cfg['door_band']
    door_inset = cfg['door_inset']
    with_hinges = with_doors and cfg['with_hinges']
    hinge = cfg.get('hinge', HINGE)
    tol = cfg['tol']

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
    cab_transform = adsk.core.Matrix3D.create()
    cab_transform.translation = adsk.core.Vector3D.create(
        translation[0], translation[1], translation[2])
    cabinet_occ = _add_new_component(root, cab_transform)
    cabinet_comp = cabinet_occ.component
    cabinet_comp.name = 'Cabinet {0}x{1}x{2}'.format(int(W), int(H), int(D))
    cabinet_comp.attributes.add(ATTR_GROUP, CABINET_CFG_ATTR, json.dumps(cfg))

    inner_w = W - 2 * t  # clear width between the sides (mm)
    warnings = []

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

    # Precompute the dado grooves (cm) when there's a back panel. Each groove is
    # cut the full 'dd' deep and 'back_t + 2*sc' wide so the back seats with
    # bottom + side clearance and never fills the whole slot.
    left_g = right_g = base_g = top_g = None
    if with_back:
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
    # boxes and interior dividers all stop at (or just short of) this plane.
    back_front_y = (D - back_setback - back_t) if with_back else D

    # Base and top: captured between the sides, thickness along Z. The SIDES are
    # created later (after the interior walk) because door hinge plates bore into
    # them and those hole positions are only known once the regions are laid out.
    add_panel('Base', (tc, 0.0, z_off, Wc - 2 * tc, Dc, tc),
              make_panel_data('Base', 'Base', inner_w, D, material), base_g)
    add_panel('Tampo', (tc, 0.0, z_off + Hbox_c - tc, Wc - 2 * tc, Dc, tc),
              make_panel_data('Tampo', 'Tampo', inner_w, D, material), top_g)

    # Back panel: reaches 'engage' (= dd - bottom clearance) into all four grooves.
    if with_back:
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

        # Front (visible) board + back rail, both spanning the full width.
        add_kick('Rodape Frente', (0.0, s_c, 0.0, Wc, kt_c, kh_c),
                 make_panel_data('Rodape', 'Rodape Frente', W, toe_kick_height, toe_kick_material))
        add_kick('Rodape Traseira', (0.0, conn_y1, 0.0, Wc, kt_c, kh_c),
                 make_panel_data('Travessa', 'Rodape Traseira', W, toe_kick_height, toe_kick_material))

        def add_kick_conn(name, x0):
            add_kick(name, (x0, conn_y0, 0.0, kt_c, conn_len_c, kh_c),
                     make_panel_data('Travessa', name, conn_len_mm, toe_kick_height, toe_kick_material))

        # End connectors, then interior reinforcements dividing the clear width
        # into equal bays no wider than toe_kick_max_span.
        add_kick_conn('Rodape Lateral E', 0.0)
        add_kick_conn('Rodape Lateral D', Wc - kt_c)

        clear_w = W - 2 * toe_kick_t                 # between the two end connectors
        n_bays = max(1, int(math.ceil(clear_w / toe_kick_max_span)))
        span_c = Wc - 2 * kt_c
        for j in range(1, n_bays):
            cx = kt_c + j * (span_c / n_bays) - kt_c / 2.0
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
    ctx.door_band = door_band
    ctx.door_gap = door_gap
    ctx.door_t = door_t
    ctx.drawer_gap = cfg['drawer_gap']
    ctx.drawer = cfg['drawer']
    ctx.slide_key = cfg['slide_key']
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
    ctx.warnings = warnings
    ctx.door_occs = []
    ctx.drawer_bundles = []
    ctx.hole_map = {}
    ctx.door_i = ctx.drawer_i = ctx.shelf_i = 0
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
                  make_panel_data(d['funcao'], d['name'], a_mm, b_mm, material),
                  None, holes)

    # Sides LAST: full box height x depth, thickness along X, with the back
    # grooves and any accumulated hinge plate holes. (anchor stays the Corpo occ.)
    add_panel('Lateral Esquerda', (0.0, 0.0, z_off, tc, Dc, Hbox_c),
              make_panel_data('Lateral', 'Lateral Esquerda', Hbox, D, material),
              left_g, ctx.hole_map.get('L'))
    add_panel('Lateral Direita', (Wc - tc, 0.0, z_off, tc, Dc, Hbox_c),
              make_panel_data('Lateral', 'Lateral Direita', Hbox, D, material),
              right_g, ctx.hole_map.get('R'))

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
LEAF_TYPES = ('open', 'shelves', 'doors', 'drawers')


def is_split(node):
    return isinstance(node, dict) and 'split' in node


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
    out.update({k: cfg[k] for k in cfg if k not in ('tol', 'hinge', 'drawer', 'layout')})
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


def add_cabinet_inputs(inputs, cfg):
    """Build the full cabinet parameter UI, pre-filled from `cfg` (mm)."""
    inputs.addValueInput('width', 'Largura (W)', 'mm', adsk.core.ValueInput.createByReal(cfg['W'] / 10.0))
    inputs.addValueInput('height', 'Altura (H)', 'mm', adsk.core.ValueInput.createByReal(cfg['H'] / 10.0))
    inputs.addValueInput('depth', 'Profundidade (D)', 'mm', adsk.core.ValueInput.createByReal(cfg['D'] / 10.0))
    inputs.addValueInput('thickness', 'Espessura', 'mm', adsk.core.ValueInput.createByReal(cfg['t'] / 10.0))
    inputs.addIntegerSpinnerCommandInput('shelves', 'Prateleiras', 0, 50, 1, int(cfg['n_shelves']))
    inputs.addBoolValueInput('shelfAlignFront', 'Prateleiras alinhadas com a frente',
                             True, '', bool(cfg.get('shelf_align_front', False)))

    material = inputs.addDropDownCommandInput(
        'material', 'Material', adsk.core.DropDownStyles.TextListDropDownStyle)
    for (name, _thk) in MATERIALS:
        material.listItems.add(name, name == cfg['material'])
    if not material.selectedItem:
        material.listItems.item(0).isSelected = True

    group = inputs.addGroupCommandInput('backGroup', 'Fundo (back panel)')
    group.isExpanded = True
    g = group.children
    g.addBoolValueInput('withBack', 'Add back panel', True, '', bool(cfg['with_back']))
    back_mat = g.addDropDownCommandInput(
        'backMaterial', 'Material do fundo', adsk.core.DropDownStyles.TextListDropDownStyle)
    for (name, _thk) in MATERIALS:
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
    for (name, _thk) in MATERIALS:
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
    for (name, _thk) in MATERIALS:
        door_mat.listItems.add(name, name == cfg['door_material'])
    if not door_mat.selectedItem:
        door_mat.listItems.item(0).isSelected = True
    dr.addValueInput('doorThickness', 'Espessura da porta', 'mm', adsk.core.ValueInput.createByReal(cfg['door_t'] / 10.0))
    dr.addIntegerSpinnerCommandInput('nDoors', 'Numero de portas', 1, 20, 1, int(cfg['n_doors']))
    dr.addBoolValueInput('doorInset', 'Porta embutida (inset)', True, '', bool(cfg['door_inset']))
    dr.addValueInput('doorGap', 'Folga (reveal)', 'mm', adsk.core.ValueInput.createByReal(cfg['door_gap'] / 10.0))
    dr.addStringValueInput('doorBand', 'Fita da porta', cfg['door_band'])
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
    for (name, _thk) in MATERIALS:
        box_mat.listItems.add(name, name == dr_cfg['box_material'])
    if not box_mat.selectedItem:
        box_mat.listItems.item(0).isSelected = True
    face_mat = dw.addDropDownCommandInput(
        'drawerFaceMaterial', 'Material da frente', adsk.core.DropDownStyles.TextListDropDownStyle)
    for (name, _thk) in MATERIALS:
        face_mat.listItems.add(name, name == dr_cfg['face_material'])
    if not face_mat.selectedItem:
        face_mat.listItems.item(0).isSelected = True
    dw.addStringValueInput('drawerFaceBand', 'Fita da frente', dr_cfg['face_band'])
    dw.addBoolValueInput('insertRealHardware', 'Inserir modelo 3D da corredica',
                         True, '', bool(cfg['insert_real_hardware']))

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
        'door_band': inputs.itemById('doorBand').value,
        'with_hinges': inputs.itemById('withHinges').value,
        'hinge': hinge,
        'with_drawers': inputs.itemById('withDrawers').value,
        'n_drawers': inputs.itemById('nDrawers').value,
        'drawer_inset': inputs.itemById('drawerInset').value,
        'drawer_gap': inputs.itemById('drawerGap').value * 10.0,
        'slide_key': _slide_key_from_label(inputs.itemById('slideKey').selectedItem.name),
        'insert_real_hardware': inputs.itemById('insertRealHardware').value,
        # Start from the DRAWER defaults so the un-exposed box/bottom specs are
        # kept, then override the materials + band shown in the dialog.
        'drawer': dict(DRAWER, **{
            'box_material': inputs.itemById('drawerBoxMaterial').selectedItem.name,
            'face_material': inputs.itemById('drawerFaceMaterial').selectedItem.name,
            'face_band': inputs.itemById('drawerFaceBand').value,
        }),
        'tol': {
            'dado_bottom_clearance': inputs.itemById('tolDadoBottom').value * 10.0,
            'dado_side_clearance': inputs.itemById('tolDadoSide').value * 10.0,
            'shelf_back_gap': inputs.itemById('tolShelfBack').value * 10.0,
            'shelf_front_setback': inputs.itemById('tolShelfFront').value * 10.0,
            'shelf_door_clearance': inputs.itemById('tolShelfDoor').value * 10.0,
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
    inputs.itemById('doorBand').value = cfg['door_band']
    inputs.itemById('withHinges').value = bool(cfg['with_hinges'])
    inputs.itemById('withDrawers').value = bool(cfg['with_drawers'])
    _select_dropdown(inputs.itemById('slideKey'), _slide_label_for_key(cfg['slide_key']))
    inputs.itemById('nDrawers').value = int(cfg['n_drawers'])
    inputs.itemById('drawerInset').value = bool(cfg['drawer_inset'])
    inputs.itemById('drawerGap').value = cfg['drawer_gap'] / 10.0
    dr_cfg = cfg['drawer']
    _select_dropdown(inputs.itemById('drawerBoxMaterial'), dr_cfg['box_material'])
    _select_dropdown(inputs.itemById('drawerFaceMaterial'), dr_cfg['face_material'])
    inputs.itemById('drawerFaceBand').value = dr_cfg['face_band']
    inputs.itemById('insertRealHardware').value = bool(cfg['insert_real_hardware'])
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


def validate_cfg(cfg):
    """Return an error message string if the config is invalid, else None."""
    cfg = normalize_cfg(cfg)   # ensure a layout is present (synth from flat if needed)
    W, H, D, t = cfg['W'], cfg['H'], cfg['D'], cfg['t']
    if W <= 2 * t or H <= 2 * t:
        return 'Largura and Altura must be larger than twice the thickness.'
    if cfg['with_back']:
        dd, bt, sb = cfg['dado_depth'], cfg['back_t'], cfg['back_setback']
        bc = cfg['tol']['dado_bottom_clearance']
        sc = cfg['tol']['dado_side_clearance']
        if dd <= 0 or dd >= t:
            return 'Ranhura depth must be > 0 and less than the carcass thickness ({0:.0f}mm).'.format(t)
        if bt <= 0:
            return 'Back panel thickness must be greater than 0.'
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
    # Interior layout: walk the region tree (same planner the builder uses, so a
    # split that validates always builds) and check that each leaf fits its band.
    kick_h = cfg['toe_kick_height'] if cfg['with_toe_kick'] else 0.0
    Hbox = H - kick_h
    if Hbox - 2 * t <= 0:
        return 'Altura leaves no clear interior height for the carcass.'
    Wc, Hbox_c, tc = W / 10.0, Hbox / 10.0, t / 10.0
    z_off = kick_h / 10.0
    back_front_y = (D - cfg['back_setback'] - cfg['back_t']) if cfg['with_back'] else D
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
    back_front_y = (D - cfg['back_setback'] - cfg['back_t']) if cfg['with_back'] else D

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
        spec = resolve_slide_spec({'slide_key': node.get('slide_key') or cfg['slide_key']})
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
        deduction = spec.get('carcass_deduction')
        if deduction is None:
            deduction = 2.0 * spec.get('side_clearance', 0.0)
        if region_inner_w - deduction <= 0:
            return ('This region is too narrow for the slide: the drawer bottom needs '
                    'at least {0:.0f}mm of clear width.'.format(deduction))
        if deduction - 2 * drawer['box_t'] <= 0:
            return ('Drawer sides are too thick for this slide (need side thickness '
                    '< {0:.0f}mm so the box clears the runners).'.format(deduction / 2.0))
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
            add_cabinet_inputs(args.command.commandInputs, DEFAULT_CFG)
            execHandler = NewCabinetExecuteHandler()
            args.command.execute.add(execHandler)
            handlers.append(execHandler)
        except:
            if ui:
                ui.messageBox('New Cabinet setup failed:\n{}'.format(traceback.format_exc()))


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
            if args.input.id != 'cabinetPick':
                return
            idx = args.input.selectedItem.index
            if 0 <= idx < len(_edit_cabinets):
                write_cabinet_inputs(args.inputs, _edit_cabinets[idx][1])
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

            # Keep the cabinet in its current spot: reuse its position, then
            # delete the old assembly and rebuild from the edited config.
            try:
                v = occ.transform.translation
                translation = (v.x, v.y, v.z)
            except:
                translation = None
            try:
                occ.deleteMe()
            except:
                pass

            _count, status, warnings = build_cabinet(design, cfg, translation)
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


class CabinetMarkingMenuHandler(adsk.core.MarkingMenuEventHandler):
    def notify(self, args):
        global _context_edit_token
        try:
            _context_edit_token = None
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
            controls = args.linearMarkingMenu.controls
            try:
                controls.addSeparator()
            except:
                pass
            controls.addCommand(cmd_def)
        except:
            # This handler fires on every right-click, so never surface errors
            # here — a failure just means no "Edit Cabinet" entry this time.
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
        'cfg': normalize_cfg(DEFAULT_CFG),
        'cabinets': _cabinet_list(design) if design else [],
        'materials': [name for name, _thk in MATERIALS],
        'slides': [{'key': k, 'desc': d} for k, d in slide_keys()],
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
    return {'cfg': normalize_cfg(DEFAULT_CFG), 'id': 'new'}


def _palette_apply(design, data):
    """Validate the edited config and (re)build the cabinet in place, returning the
    new cabinet's token + the refreshed cabinet list so the editor can re-select it."""
    cfg = normalize_cfg(data.get('cfg') or {})
    err = validate_cfg(cfg)
    if err:
        return {'ok': False, 'error': err}
    token = data.get('id')
    translation = None
    if token and token != 'new':
        occ = _find_occ_by_token(design, token)
        if occ:
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
    return {'ok': True, 'status': status, 'warnings': list(warnings),
            'id': _new_root_token(design, before), 'cabinets': _cabinet_list(design)}


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
            inputs = args.command.commandInputs
            inputs.addTextBoxCommandInput(
                'info', '',
                'O editor de layout do armario abriu em um painel lateral.\n'
                'Divida o interior em regioes (prateleiras, portas, gavetas) e '
                'clique Aplicar para (re)gerar o armario.', 4, True)
            _show_layout_palette()
        except Exception:
            if ui:
                ui.messageBox('Cabinet Layout setup failed:\n{}'.format(traceback.format_exc()))


# -----------------------------------------------------------------------------
# Add-in lifecycle
# -----------------------------------------------------------------------------
def _add_command(panel, cmd_id, name, desc, created_handler, icon_name, promoted=False):
    cmd_def = ui.commandDefinitions.itemById(cmd_id)
    if not cmd_def:
        resource_folder = res(icon_name)
        if resource_folder:
            cmd_def = ui.commandDefinitions.addButtonDefinition(cmd_id, name, desc, resource_folder)
        else:
            cmd_def = ui.commandDefinitions.addButtonDefinition(cmd_id, name, desc)
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
        _add_command(panel, NEW_CABINET_CMD_ID, 'New Cabinet',
                     'Create a cabinet carcass with shelves',
                     NewCabinetCreatedHandler(), 'new_cabinet', promoted=True)
        _add_command(panel, EDIT_CABINET_CMD_ID, 'Edit Cabinet',
                     'Edit a cabinet and regenerate it',
                     EditCabinetCreatedHandler(), 'edit_cabinet', promoted=True)
        _add_command(panel, LAYOUT_CMD_ID, 'Cabinet Layout',
                     'Visually divide the cabinet interior into regions',
                     CabinetLayoutCreatedHandler(), 'edit_cabinet', promoted=True)
        _add_command(panel, EXPORT_CMD_ID, 'Export Cut List',
                     'Export all panels as a CorteCloud CSV',
                     ExportCutListCreatedHandler(), 'export', promoted=True)

        # Add "Edit Cabinet" to the right-click menu when a cabinet is clicked.
        global _marking_menu_handler
        _marking_menu_handler = CabinetMarkingMenuHandler()
        ui.markingMenuDisplaying.add(_marking_menu_handler)
        handlers.append(_marking_menu_handler)
    except:
        if ui:
            ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))


def stop(context):
    global _marking_menu_handler, _layout_palette_handler
    try:
        if _marking_menu_handler:
            try:
                ui.markingMenuDisplaying.remove(_marking_menu_handler)
            except:
                pass
            _marking_menu_handler = None

        # Tear down the layout palette.
        pal = ui.palettes.itemById(LAYOUT_PALETTE_ID)
        if pal:
            try:
                pal.deleteMe()
            except:
                pass
        _layout_palette_handler = None

        workspace = ui.workspaces.itemById(WORKSPACE_ID)
        tab = workspace.toolbarTabs.itemById(TAB_ID)
        if tab:
            panel = tab.toolbarPanels.itemById(PANEL_ID)
            if panel:
                for cmd_id in (NEW_PANEL_CMD_ID, NEW_CABINET_CMD_ID,
                               EDIT_CABINET_CMD_ID, LAYOUT_CMD_ID, EXPORT_CMD_ID):
                    ctrl = panel.controls.itemById(cmd_id)
                    if ctrl:
                        ctrl.deleteMe()
                panel.deleteMe()
            tab.deleteMe()

        for cmd_id in (NEW_PANEL_CMD_ID, EXPORT_CMD_ID):
            cmd_def = ui.commandDefinitions.itemById(cmd_id)
            if cmd_def:
                cmd_def.deleteMe()
    except:
        if ui:
            ui.messageBox('Stop failed:\n{}'.format(traceback.format_exc()))
