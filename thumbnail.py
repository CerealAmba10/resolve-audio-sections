"""Thumbnail frames: a background picture with a tilted title, "TEASER: N", a logo and an extra text line.

Styled like the Resolve Text+ layout it replaces: a fill, a thin black edge at 60%, a thick outer outline
and a soft black shadow (pink title with a white outline; white label with a cyan outline).
"""
import os
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

PINK, WHITE, CYAN = (254, 78, 200), (255, 255, 255), (39, 179, 255)
KOMIKA = ('KOMIKAX.ttf', 'KOMIKAX_.ttf', 'Komika Axis.ttf')
FALLBACK_FONTS = ('ariblk.ttf', 'impact.ttf')


def font_folders():
    return [Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts', Path(os.environ.get('LOCALAPPDATA', '')) / 'Microsoft/Windows/Fonts']


def installed(names):
    for folder in font_folders():
        for name in names:
            if (folder / name).is_file():
                return str(folder / name)
        stems = [n.rsplit('.', 1)[0].lower().replace(' ', '').replace('-regular', '') for n in names]
        found = folder.is_dir() and next((p for p in folder.glob('*.[ot]t[fc]')
                                          if p.stem.lower().replace(' ', '').replace('-regular', '') in stems), None)
        if found:
            return str(found)


def find_font(kind='label'):
    """Komika Axis (title and label) if installed, else a heavy Windows font. Returns (path, is_komika)."""
    wanted = (KOMIKA,)
    for names in wanted:
        path = installed(names)
        if path:
            return path, names is wanted[0]
    return installed(FALLBACK_FONTS), False


def load_titles(path):
    """One title per non-empty line of a text file."""
    lines = Path(path).read_text(encoding='utf-8-sig', errors='replace').splitlines()
    return [line.strip() for line in lines if line.strip()]


def deal_titles(titles, count, rng=random):
    """`count` titles in random order, using every line before any repeats."""
    dealt = []
    while titles and len(dealt) < count:
        batch = list(titles)
        rng.shuffle(batch)
        dealt += batch
    return dealt[:count]


def cover(picture, width, height):
    """Scale and centre-crop the background to fill the frame."""
    picture = picture.convert('RGB')
    scale = max(width / picture.width, height / picture.height)
    picture = picture.resize((max(width, round(picture.width * scale)), max(height, round(picture.height * scale))),
                             Image.LANCZOS)
    left, top = (picture.width - width) // 2, (picture.height - height) // 2
    return picture.crop((left, top, left + width, top + height))


def wrap(text, font, max_width, draw):
    """Greedy word wrap at a given font."""
    lines, line = [], ''
    for word in text.split():
        trial = f'{line} {word}'.strip()
        if line and draw.textlength(trial, font=font) > max_width:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + ([line] if line else [])


def styled(lines, font, size, fill, outline, spacing=1.0, bold=0.025, ring=0.07, outer=0.09, glow=0.12,
           glow_color=(0, 0, 0), glow_strength=0.85, glow_spread=0.0, tracking=0.0,
           rim_color=(0, 0, 0), rim_alpha=255, top_glow=0.0):
    """Text block on transparency, layered like the Resolve Text+ elements it copies. Back to front:
    a soft glow around the word (black like Text+'s shadow with softness, or light for the Canva title), the outer outline (`outer` beyond the
    letters), the fill (thickened by `bold`; Komika Axis alone reads thin), and a black outline at 60%
    (`ring` wide) centred on the letter edge as Fusion draws it, so it darkens the fill's rim and the
    outline's inner edge. Widths are fractions of the font size."""
    draw = ImageDraw.Draw(Image.new('L', (1, 1)))
    bold = round(size * bold)
    half = max(1, round(size * ring / 2))
    outer = bold + max(2, round(size * outer))
    blur = size * glow
    pad = outer + round(blur * 2.5) + round(size * 0.2)
    track = size * tracking          # letter spacing; negative pulls letters together, keeping the font's kerning
    widths = [draw.textlength(line, font=font) + track * (len(line) - 1) for line in lines]
    step = round(size * spacing)
    width, height = int(max(widths)) + 2 * pad, step * (len(lines) - 1) + round(size * 1.1) + 2 * pad

    def mask(grow):
        layer = Image.new('L', (width, height), 0)
        d = ImageDraw.Draw(layer)
        for n, (line, w) in enumerate(zip(lines, widths)):
            x, y = (width - w) / 2, pad + n * step
            if not track:
                d.text((x, y), line, font=font, fill=255, stroke_width=grow, stroke_fill=255)
                continue
            for i, letter in enumerate(line):
                d.text((x + draw.textlength(line[:i], font=font) + track * i, y), letter, font=font, fill=255,
                       stroke_width=grow, stroke_fill=255)
        return layer

    block = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    around = mask(outer)
    halo = mask(outer + round(size * glow_spread)) if glow_spread else around       # a harder glow starts wider
    block.paste(glow_color + (255,), (0, 0), halo.filter(ImageFilter.GaussianBlur(blur)).point(lambda v: v * glow_strength))
    block.paste(outline + (255,), (0, 0), around)
    # Fill and dark rim letter by letter, left to right, so where tightly spaced letters overlap each one
    # keeps its own edge over its neighbour instead of the word melting into one shape.
    margin = bold + 2 * half + 2
    for n, (line, w) in enumerate(zip(lines, widths)):
        x0, y = (width - w) / 2, pad + n * step
        for i, letter in enumerate(line):
            if not letter.strip():
                continue
            x = x0 + draw.textlength(line[:i], font=font) + track * i
            left, top = int(x) - margin, int(y) - margin
            right, bottom = font.getbbox(letter, stroke_width=bold)[2:]      # the letter's real extent (Komika sits low)
            local = Image.new('L', (right + 2 * margin + 2, bottom + 2 * margin + 2), 0)
            ImageDraw.Draw(local).text((x - left, y - top), letter, font=font, fill=255, stroke_width=bold, stroke_fill=255)
            rim = ImageChops.subtract(local, local.filter(ImageFilter.MinFilter(2 * half + 1)))
            block.paste(fill + (255,), (left, top), local)
            edge = Image.new('RGBA', local.size, (0, 0, 0, 0))
            edge.paste(rim_color + (rim_alpha,), (0, 0), rim)
            block.alpha_composite(edge, (left, top))
    if top_glow:
        # A glow of the fill colour laid over the finished text, blooming from the letters across the outlines.
        bloom = Image.new('RGBA', (width, height), fill + (0,))
        bloom.putalpha(mask(bold).filter(ImageFilter.GaussianBlur(size * 0.12)).point(lambda v: int(v * top_glow)))
        block.alpha_composite(bloom)
    return block


# Where each element sits: centre (x, y) as fractions of the frame, a size multiplier, rotation in degrees.
# The extra text also carries its words in 'value'.
DEFAULT_LAYOUT = {
    'title': dict(x=0.30, y=0.24, size=1.0, angle=3.0, visible=True),
    'label': dict(x=0.84, y=0.13, size=1.0, angle=0.0, visible=True),
    'logo': dict(x=0.87, y=0.79, size=1.0, angle=0.0, visible=True),
    'text': dict(x=0.17, y=0.87, size=1.0, angle=0.0, visible=True, value=''),
}
ELEMENTS = tuple(DEFAULT_LAYOUT)
MAX_TEXT_LINES = 3
_blocks, _backgrounds = {}, {}


def layout_with(layout=None, **changes):
    """A complete layout: defaults, overlaid with `layout`, then with per-element `changes`."""
    merged = {kind: dict(spec) for kind, spec in DEFAULT_LAYOUT.items()}
    for source in (layout or {}, changes):
        for kind, spec in source.items():
            if kind in merged:
                merged[kind].update(spec)
    return merged


def mirrored(layout):
    """Left-right mirror image of a layout (positions and tilts)."""
    return {kind: dict(spec, x=round(1 - spec['x'], 4), angle=-spec['angle']) for kind, spec in layout_with(layout).items()}


def _tilted(block, angle):
    return block.rotate(angle, resample=Image.BICUBIC, expand=True) if angle else block


def _title_block(title, width, height, scale, font_path):
    """Pink title wrapped to fit its box (three lines like the example, four if it must)."""
    draw = ImageDraw.Draw(Image.new('L', (1, 1)))
    box_w, box_h = width * 0.5 * scale, height * 0.45 * scale

    def fits(size, most):
        font = ImageFont.truetype(font_path, size)
        lines = wrap(title, font, box_w, draw)
        block_h = round(size * 0.95) * (len(lines) - 1) + size
        ok = len(lines) <= most and block_h <= box_h and max(draw.textlength(l, font=font) for l in lines) <= box_w
        return ok and (font, lines)

    sizes = range(max(10, round(height * 0.16 * scale)), 8, -2)
    font, lines = next((fit for s in sizes if s >= height * 0.08 * scale for fit in [fits(s, 3)] if fit), None) or         next(fit for s in sizes for fit in [fits(s, 4) or (s <= 10 and fits(s, 99))] if fit)
    size = font.size * 2       # drawn at double size; scaled down after tilting so the edges stay crisp
    return styled(lines, ImageFont.truetype(font_path, size), size, PINK, WHITE, spacing=0.95, bold=0.04, top_glow=0.55)


def element_block(kind, spec, content, width, height, fonts):
    """One element drawn, sized and tilted, ready to paste centred on its position (cached apart from position)."""
    key = (kind, content, round(spec['size'], 3), round(spec['angle'], 2), width, height, fonts)
    if key in _blocks:
        return _blocks[key]
    if len(_blocks) > 400:
        _blocks.clear()
    title_font, label_font = fonts
    block = None
    if kind == 'title' and content:
        block = _title_block(content.upper(), width, height, spec['size'], title_font)
    elif kind == 'label' and content:
        size = round(height * 0.075 * spec['size']) * 2
        block = styled([content.upper()], ImageFont.truetype(label_font, size), size, WHITE, CYAN, bold=0.04)
    elif kind == 'text' and content:
        size = round(height * 0.11 * spec['size']) * 2
        impact = installed(('impact.ttf',)) or label_font
        lines = [line for line in content.splitlines() if line.strip()][:MAX_TEXT_LINES] or [content]
        block = styled(lines, ImageFont.truetype(impact, size), size, WHITE, (0, 0, 0), spacing=1.05, bold=0, outer=0.06,
                       glow=0.04, glow_strength=0.35, rim_alpha=0)
    elif kind == 'logo' and content:
        picture = Image.open(content).convert('RGBA')
        tall = max(2, round(height * 0.22 * spec['size'])) * 2
        block = picture.resize((max(2, round(picture.width * tall / picture.height)), tall), Image.LANCZOS)
    if block is not None:
        block = _tilted(block, spec['angle'])
        block = block.resize((max(1, block.width // 2), max(1, block.height // 2)), Image.LANCZOS)
    _blocks[key] = block
    return block


def compose(background, title, label, width, height, layout=None, logo=None, font_path=None, label_font=None):
    """The thumbnail frame and each visible element's box {kind: (left, top, right, bottom)} in pixels.
    `label` (e.g. "TEASER: 12") and the extra text are left off when empty; fonts default to Komika Axis."""
    fonts = (font_path or find_font('title')[0], label_font or find_font('label')[0])
    layout = layout_with(layout)
    key = (str(background), width, height)
    if key not in _backgrounds:
        if len(_backgrounds) > 8:
            _backgrounds.clear()
        _backgrounds[key] = cover(Image.open(background), width, height)
    frame = _backgrounds[key].copy()
    contents = dict(title=title or '', label=label or '', logo=str(logo) if logo else '', text=layout['text'].get('value', ''))
    boxes = {}
    for kind in ('logo', 'text', 'title', 'label'):
        spec = layout[kind]
        block = spec['visible'] and element_block(kind, spec, contents[kind], width, height, fonts)
        if block:
            left, top = round(spec['x'] * width - block.width / 2), round(spec['y'] * height - block.height / 2)
            # Nudge back inside the frame if the visible part would spill over an edge (three lines of text, a big logo).
            x0, y0, x1, y1 = block.getbbox() or (0, 0, block.width, block.height)
            left = min(max(left, -x0), width - x1)
            top = min(max(top, -y0), height - y1)
            frame.paste(block, (left, top), block)
            boxes[kind] = (left, top, left + block.width, top + block.height)
    return frame, boxes


def render(background, title, label, width, height, font_path=None, label_font=None, layout=None, logo=None):
    """The thumbnail frame (see compose)."""
    return compose(background, title, label, width, height, layout, logo, font_path, label_font)[0]


if __name__ == '__main__':
    import sys
    rng = random.Random(1)
    assert deal_titles(['a', 'b', 'c'], 5, rng)[:3] and sorted(deal_titles(['a', 'b', 'c'], 3, rng)) == ['a', 'b', 'c']
    assert deal_titles([], 3) == []
    picture = render(sys.argv[1], sys.argv[2], sys.argv[3], 1920, 1080)
    picture.save(sys.argv[4])
    print('rendered', sys.argv[4], 'font', find_font())
