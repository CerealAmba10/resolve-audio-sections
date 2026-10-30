"""Thumbnail frames: a background picture, a big tilted title top-left and "TEASER: N" top-right.

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


def render(background, title, label, width, height, font_path=None, tilt=3.0, label_font=None):
    """The thumbnail frame. `label` (e.g. "TEASER: 12") goes top-right; empty to leave it off.
    `font_path` is the title font, `label_font` the label's; both default to Komika Axis when installed."""
    font_path = font_path or find_font('title')[0]
    label_font = label_font or find_font('label')[0]
    frame = cover(Image.open(background), width, height)
    draw = ImageDraw.Draw(frame)
    title = (title or '').upper()
    if title:
        # Biggest size whose wrapped block fits the top-left box: three lines like the example, four if it must.
        box_w, box_h = width * 0.5, height * 0.45

        def fits(size, most):
            font = ImageFont.truetype(font_path, size)
            lines = wrap(title, font, box_w, draw)
            block_h = round(size * 0.95) * (len(lines) - 1) + size
            ok = len(lines) <= most and block_h <= box_h and max(draw.textlength(l, font=font) for l in lines) <= box_w
            return ok and (font, lines)

        sizes = range(round(height * 0.16), 8, -2)
        font, lines = next((fit for s in sizes if s >= height * 0.08 for fit in [fits(s, 3)] if fit), None) or \
            next(fit for s in sizes for fit in [fits(s, 4) or (s <= 10 and fits(s, 99))] if fit)
        size = font.size
        # Same crisp Text+ styling as the label, in pink with a white outline. Drawn at double size, tilted,
        # then scaled down, so the rotation doesn't soften the edges.
        big = styled(lines, ImageFont.truetype(font_path, size * 2), size * 2, PINK, WHITE, spacing=0.95, bold=0.04,
                     top_glow=0.55)
        big = big.rotate(tilt, resample=Image.BICUBIC, expand=True)
        block = big.resize((big.width // 2, big.height // 2), Image.LANCZOS)
        frame.paste(block, (round(width * 0.03 - size * 0.4), round(height * 0.06 - size * 0.75)), block)
    if label:
        size = round(height * 0.075)
        font = ImageFont.truetype(label_font, size)
        block = styled([label.upper()], font, size, WHITE, CYAN, bold=0.04)
        frame.paste(block, (round(width * 0.95 - block.width + size * 0.5), round(height * 0.09 - size * 0.5)), block)
    return frame


if __name__ == '__main__':
    import sys
    rng = random.Random(1)
    assert deal_titles(['a', 'b', 'c'], 5, rng)[:3] and sorted(deal_titles(['a', 'b', 'c'], 3, rng)) == ['a', 'b', 'c']
    assert deal_titles([], 3) == []
    picture = render(sys.argv[1], sys.argv[2], sys.argv[3], 1920, 1080)
    picture.save(sys.argv[4])
    print('rendered', sys.argv[4], 'font', find_font())
