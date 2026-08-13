"""In-memory expansion of ``<use>`` elements.

Handles two distinct ``<use>`` syntaxes:

1. ``<use data-icon="lib/name">`` — project-internal icon placeholder.
   ``finalize_svg`` already expands it on disk into ``svg_final/``; this
   module provides the same expansion in memory so ``svg_to_pptx`` can
   consume ``svg_output/`` directly without first running the on-disk
   finalize step. The heavy lifting is delegated to
   ``svg_finalize.embed_icons`` so the two pipelines stay behaviourally
   aligned.

2. ``<use href="#id">`` / ``<use xlink:href="#id">`` — standard SVG
   internal references. These reference shapes defined anywhere in the
   tree (commonly inside ``<defs>``). ``expand_use_href_references``
   resolves each reference, deep-copies the target, applies the ``<use>``
   element's positional/transform attributes, and replaces the ``<use>``
   in-place with the resolved copy.

Public API:
    expand_use_data_icons(root, icons_dir) -> int
    expand_use_href_references(root) -> int
    expand_all_use_elements(root, icons_dir) -> int
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from xml.etree import ElementTree as ET


SVG_NS = 'http://www.w3.org/2000/svg'
XLINK_NS = 'http://www.w3.org/1999/xlink'


def _import_embed_icons():
    """Lazy import so svg_to_pptx doesn't hard-require svg_finalize at import time."""
    scripts_dir = Path(__file__).resolve().parent.parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from svg_finalize import embed_icons  # type: ignore
    return embed_icons


def _build_replacement_g(
    use_elem: ET.Element,
    icons_dir: Path,
    embed_icons_mod,
) -> ET.Element | None:
    """Resolve a single ``<use data-icon="...">`` into an expanded ``<g>``.

    Returns None when the icon name is missing, unresolved, or the icon
    file cannot be parsed. Callers should leave the original ``<use>`` in
    place in that case (matching the on-disk finalize_svg behaviour, which
    also leaves unresolvable placeholders untouched).
    """
    use_str = ET.tostring(use_elem, encoding='unicode')
    attrs = embed_icons_mod.parse_use_element(use_str)
    if 'icon' not in attrs:
        return None

    icon_path, _base_size = embed_icons_mod.resolve_icon_path(
        attrs['icon'], icons_dir,
    )
    if not icon_path.exists():
        return None

    color = attrs.get('fill', '#000000')
    elements, style, base_size = embed_icons_mod.extract_paths_from_icon(
        icon_path, color,
    )
    if not elements:
        return None

    g_xml = embed_icons_mod.generate_icon_group(attrs, elements, style, base_size)

    # Wrap with a namespaced root so the parsed subtree carries the SVG
    # namespace through to every primitive (path/circle/...).
    wrapped = f'<svg xmlns="{SVG_NS}">{g_xml}</svg>'
    try:
        parsed_root = ET.fromstring(wrapped)
    except ET.ParseError:
        return None

    for child in parsed_root:
        local = child.tag.split('}')[-1] if '}' in child.tag else child.tag
        if local == 'g':
            return child
    return None


def expand_use_data_icons(root: ET.Element, icons_dir: Path) -> int:
    """Replace every ``<use data-icon="...">`` in *root* with its expansion.

    Walks the tree, finds use elements that carry a ``data-icon`` attribute,
    builds a new ``<g>`` subtree from the corresponding icon library, and
    swaps it into the parent element at the same position.

    Returns the number of placeholders successfully expanded. Unresolvable
    placeholders are left in place so callers can decide whether to warn.
    """
    if not icons_dir.exists():
        return 0

    embed_icons_mod = _import_embed_icons()

    # ElementTree elements don't carry a parent reference, so build a map.
    parent_of: dict[ET.Element, ET.Element] = {}
    for parent in root.iter():
        for child in parent:
            parent_of[child] = parent

    targets: list[ET.Element] = []
    for elem in root.iter():
        local = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
        if local == 'use' and elem.get('data-icon'):
            targets.append(elem)

    expanded = 0
    for use_elem in targets:
        parent = parent_of.get(use_elem)
        if parent is None:
            continue
        replacement = _build_replacement_g(use_elem, icons_dir, embed_icons_mod)
        if replacement is None:
            continue
        idx = list(parent).index(use_elem)
        parent.remove(use_elem)
        parent.insert(idx, replacement)
        expanded += 1

    return expanded


# ---------------------------------------------------------------------------
# Standard SVG <use href="#id"> / <use xlink:href="#id"> expansion
# ---------------------------------------------------------------------------

def _build_id_map(root: ET.Element) -> dict[str, ET.Element]:
    """Return a mapping of id -> element for every element in the tree."""
    id_map: dict[str, ET.Element] = {}
    for elem in root.iter():
        elem_id = elem.get('id')
        if elem_id:
            id_map[elem_id] = elem
    return id_map


def _get_href(use_elem: ET.Element) -> str | None:
    """Return the href value from a ``<use>`` element (plain or xlink)."""
    href = use_elem.get('href') or use_elem.get(f'{{{XLINK_NS}}}href')
    return href


def _symbol_to_g(
    symbol: ET.Element,
    use_x: float,
    use_y: float,
    use_w: float | None,
    use_h: float | None,
    use_transform: str | None,
) -> ET.Element:
    """Convert a ``<symbol>`` reference into a positioned ``<g>`` element.

    A ``<symbol>`` owns its own ``viewBox`` coordinate space. When the
    ``<use>`` specifies ``width``/``height``, we compute a scale transform
    that maps the symbol's viewBox into the requested dimensions. The
    symbol's children are wrapped in a ``<g>`` that carries the combined
    translate + scale (+ optional outer transform from the ``<use>``).
    """
    viewbox_str = symbol.get('viewBox', '')
    sym_children = list(symbol)

    # Build the inner transform: translate(use_x, use_y) [scale(sx, sy)]
    inner_parts: list[str] = []

    if viewbox_str and use_w is not None and use_h is not None:
        parts = viewbox_str.replace(',', ' ').split()
        if len(parts) == 4:
            try:
                vb_min_x, vb_min_y, vb_w, vb_h = (float(p) for p in parts)
                sx = use_w / vb_w if vb_w else 1.0
                sy = use_h / vb_h if vb_h else 1.0
                # Translate to use position, then compensate for viewBox origin
                tx = use_x - vb_min_x * sx
                ty = use_y - vb_min_y * sy
                if tx or ty:
                    inner_parts.append(f'translate({tx:g},{ty:g})')
                if sx != 1.0 or sy != 1.0:
                    inner_parts.append(f'scale({sx:g},{sy:g})')
            except ValueError:
                pass

    if not inner_parts:
        # No viewBox or no use dimensions: plain translate
        if use_x or use_y:
            inner_parts.append(f'translate({use_x:g},{use_y:g})')

    inner_g = ET.Element(f'{{{SVG_NS}}}g')
    if inner_parts:
        inner_g.set('transform', ' '.join(inner_parts))
    for child in sym_children:
        inner_g.append(copy.deepcopy(child))

    if use_transform:
        outer_g = ET.Element(f'{{{SVG_NS}}}g')
        outer_g.set('transform', use_transform)
        outer_g.append(inner_g)
        return outer_g

    return inner_g


def _regular_elem_to_g(
    elem: ET.Element,
    use_x: float,
    use_y: float,
    use_transform: str | None,
) -> ET.Element:
    """Wrap a deep-copied regular element with translate/transform from ``<use>``.

    ``width``/``height`` on a ``<use>`` that references a non-symbol element
    are ignored per the SVG spec.
    """
    copied = copy.deepcopy(elem)
    # Remove id to avoid duplicate ids in the output
    if 'id' in copied.attrib:
        del copied.attrib['id']

    needs_translate = bool(use_x or use_y)
    needs_outer = bool(use_transform)

    if not needs_translate and not needs_outer:
        return copied

    if needs_translate:
        inner_g = ET.Element(f'{{{SVG_NS}}}g')
        inner_g.set('transform', f'translate({use_x:g},{use_y:g})')
        inner_g.append(copied)
        copied = inner_g

    if needs_outer:
        outer_g = ET.Element(f'{{{SVG_NS}}}g')
        outer_g.set('transform', use_transform)  # type: ignore[arg-type]
        outer_g.append(copied)
        return outer_g

    return copied


def expand_use_href_references(root: ET.Element) -> int:
    """Replace standard ``<use href="#id">`` elements with their targets.

    Handles both ``href`` and the legacy ``xlink:href`` attribute forms.
    Skips ``<use data-icon="...">`` elements (handled by
    ``expand_use_data_icons``). Skips external references (href not starting
    with ``#``) and unresolvable ids.

    ``<symbol>`` targets are treated specially: their ``viewBox`` is used to
    compute a scale transform mapping the symbol's coordinate space into the
    ``width``/``height`` requested by the ``<use>`` element.

    Returns the number of replacements made.
    """
    id_map = _build_id_map(root)

    # Build parent map (ElementTree has no parent pointer).
    parent_of: dict[ET.Element, ET.Element] = {}
    for parent in root.iter():
        for child in parent:
            parent_of[child] = parent

    targets: list[ET.Element] = []
    for elem in root.iter():
        local = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
        if local != 'use':
            continue
        if elem.get('data-icon'):
            continue  # handled by expand_use_data_icons
        targets.append(elem)

    expanded = 0
    for use_elem in targets:
        parent = parent_of.get(use_elem)
        if parent is None:
            continue

        href = _get_href(use_elem)
        if not href or not href.startswith('#'):
            # Unresolvable or external href: replace with empty <g> to prevent export failure
            idx = list(parent).index(use_elem)
            parent.remove(use_elem)
            parent.insert(idx, ET.Element(f'{{{SVG_NS}}}g'))
            expanded += 1
            continue

        ref_id = href[1:]  # strip leading '#'
        referenced = id_map.get(ref_id)
        if referenced is None:
            # Unresolvable id: replace with empty <g> to prevent export failure
            idx = list(parent).index(use_elem)
            parent.remove(use_elem)
            parent.insert(idx, ET.Element(f'{{{SVG_NS}}}g'))
            expanded += 1
            continue

        use_x = float(use_elem.get('x') or 0)
        use_y = float(use_elem.get('y') or 0)
        use_w_str = use_elem.get('width')
        use_h_str = use_elem.get('height')
        use_w = float(use_w_str) if use_w_str is not None else None
        use_h = float(use_h_str) if use_h_str is not None else None
        use_transform = use_elem.get('transform') or None

        ref_local = referenced.tag.split('}')[-1] if '}' in referenced.tag else referenced.tag

        if ref_local == 'symbol':
            replacement = _symbol_to_g(
                referenced, use_x, use_y, use_w, use_h, use_transform,
            )
        else:
            replacement = _regular_elem_to_g(referenced, use_x, use_y, use_transform)

        idx = list(parent).index(use_elem)
        parent.remove(use_elem)
        parent.insert(idx, replacement)
        expanded += 1

    return expanded


def expand_all_use_elements(root: ET.Element, icons_dir: Path) -> int:
    """Expand all ``<use>`` variants in *root* and return the total count.

    Calls ``expand_use_data_icons`` for project-internal icon placeholders,
    then ``expand_use_href_references`` for standard SVG internal references.
    """
    count = expand_use_data_icons(root, icons_dir)
    count += expand_use_href_references(root)
    return count
