"""Thumbnail layout editor shown before rendering: drag, size and tilt the title, TEASER label, logo and extra
text in a large preview, for the whole batch or for one thumbnail at a time."""
import random
import tkinter as tk
from tkinter import ttk

from PIL import ImageTk

from thumbnail import compose, layout_with, load_titles, mirrored

TILE_W, TILE_H = 224, 126
VIEW_W, VIEW_H = 864, 486
NAMES = (('title', 'Title'), ('label', 'TEASER'), ('logo', 'Logo'), ('text', 'Extra text'))


class ThumbnailReview:
    def __init__(self, master, jobs, thumb, on_render, on_back, finish_label=None):
        """`jobs` carry name, title and teaser_index; a job's own layout goes in job['layout'], the batch layout in
        thumb['layout']. Both are changed in place."""
        self.jobs, self.thumb, self.on_render, self.on_back = jobs, thumb, on_render, on_back
        thumb['layout'] = layout_with(thumb.get('layout'))
        self.current, self.boxes, self.drag, self.quiet, self.queued = 0, {}, None, False, False
        self.tile_images, self.tiles, self.pending, self.filling = {}, [], [], False

        self.win = tk.Toplevel(master)
        self.win.title('Review thumbnails')
        self.win.geometry(f'{TILE_W + VIEW_W + 330}x{VIEW_H + 170}')
        self.win.protocol('WM_DELETE_WINDOW', self.back)
        self.win.lift()
        self.win.focus_force()
        ttk.Label(self.win, padding=(14, 10, 14, 4), text=f'{len(jobs)} thumbnails. Pick one on the left; drag things in '
                  'the preview to move them, double-click one to change its text, and use the controls on the right for size and tilt.').pack(anchor='w')
        body = ttk.Frame(self.win, padding=(14, 4))
        body.pack(fill='both', expand=True)

        strip = ttk.Frame(body)
        strip.pack(side='left', fill='y')
        self.list = tk.Canvas(strip, width=TILE_W + 8, highlightthickness=0)
        bar = ttk.Scrollbar(strip, orient='vertical', command=self.list.yview)
        self.list.configure(yscrollcommand=bar.set)
        bar.pack(side='right', fill='y')
        self.list.pack(side='left', fill='y')
        cells = ttk.Frame(self.list)
        self.list.create_window((0, 0), window=cells, anchor='nw')
        cells.bind('<Configure>', lambda e: self.list.configure(scrollregion=self.list.bbox('all')))
        self.list.bind('<MouseWheel>', lambda e: self.list.yview_scroll(-1 if e.delta > 0 else 1, 'units'))
        for n, job in enumerate(jobs):
            cell = ttk.Frame(cells, padding=3)
            cell.pack()
            picture = tk.Label(cell, width=TILE_W, height=TILE_H, background='#222', cursor='hand2', bd=2, relief='flat')
            picture.pack()
            caption = ttk.Label(cell)
            caption.pack(anchor='w')
            picture.bind('<Button-1>', lambda e, n=n: self.select(n))
            picture.bind('<MouseWheel>', lambda e: self.list.yview_scroll(-1 if e.delta > 0 else 1, 'units'))
            self.tiles.append((picture, caption))
            self.pending.append(n)

        self.view = tk.Canvas(body, width=VIEW_W, height=VIEW_H, background='#111', highlightthickness=0, cursor='fleur')
        self.view.pack(side='left', padx=12, anchor='n')
        self.view.bind('<ButtonPress-1>', self.press)
        self.view.bind('<B1-Motion>', self.move)
        self.view.bind('<ButtonRelease-1>', lambda e: setattr(self, 'drag', None))
        self.view.bind('<Double-Button-1>', self.edit_text)

        side = ttk.Frame(body, width=300)
        side.pack(side='left', fill='y', anchor='n')
        self.scope = tk.StringVar(value='all')
        scope = ttk.LabelFrame(side, text='Changes apply to', padding=6)
        scope.pack(fill='x')
        ttk.Radiobutton(scope, text='All thumbnails', value='all', variable=self.scope, command=self.show_controls).pack(anchor='w')
        ttk.Radiobutton(scope, text='This thumbnail only', value='one', variable=self.scope, command=self.show_controls).pack(anchor='w')
        self.kind = tk.StringVar(value='title')
        element = ttk.LabelFrame(side, text='Element', padding=6)
        element.pack(fill='x', pady=(8, 0))
        for value, text in NAMES:
            ttk.Radiobutton(element, text=text, value=value, variable=self.kind, command=self.show_controls).pack(anchor='w')
        controls = ttk.LabelFrame(side, text='Selected element', padding=6)
        controls.pack(fill='x', pady=(8, 0))
        self.visible = tk.BooleanVar(value=True)
        ttk.Checkbutton(controls, text='Show it', variable=self.visible, command=lambda: self.change(visible=self.visible.get())).pack(anchor='w')
        self.size = tk.DoubleVar(value=1.0)
        self.size_text = tk.StringVar()
        ttk.Label(controls, textvariable=self.size_text).pack(anchor='w', pady=(6, 0))
        ttk.Scale(controls, from_=0.3, to=3.0, variable=self.size, command=lambda v: self.change(size=round(float(v), 2))).pack(fill='x')
        self.angle = tk.DoubleVar(value=0.0)
        self.angle_text = tk.StringVar()
        ttk.Label(controls, textvariable=self.angle_text).pack(anchor='w', pady=(6, 0))
        ttk.Scale(controls, from_=-45, to=45, variable=self.angle, command=lambda v: self.change(angle=round(float(v), 1))).pack(fill='x')
        self.words_text = tk.StringVar()
        ttk.Label(controls, textvariable=self.words_text).pack(anchor='w', pady=(6, 0))
        self.words = tk.Text(controls, width=28, height=3, font=('Segoe UI', 9), wrap='none')
        self.words.pack(fill='x')
        self.words.bind('<KeyRelease>', lambda e: self.type_text())
        actions = ttk.Frame(side)
        actions.pack(fill='x', pady=(10, 0))
        if thumb.get('titles'):
            ttk.Button(actions, text='New random title', command=self.random_title).pack(fill='x', pady=(0, 4))
        ttk.Button(actions, text='Mirror this thumbnail', command=self.mirror).pack(fill='x')
        self.reset_button = ttk.Button(actions, text='Reset this one to the batch layout', command=self.reset)
        self.reset_button.pack(fill='x', pady=(4, 0))

        bottom = ttk.Frame(self.win, padding=(14, 8))
        bottom.pack(fill='x', side='bottom')
        ttk.Button(bottom, text='Back', command=self.back).pack(side='left')
        ttk.Button(bottom, text=finish_label or f'Render {len(jobs)} clips', command=self.render).pack(side='right')
        self.select(0)
        self.filling = True
        self.win.after(10, self.draw_tiles)

    # ---------- layouts ----------
    def layout_of(self, n):
        return self.jobs[n].get('layout') or self.thumb['layout']

    def picture(self, n, width, height):
        job = self.jobs[n]
        return compose(self.thumb['background'], job['title'], self.label_of(n), width, height,
                       self.layout_of(n), self.thumb.get('logo') or None, self.thumb.get('title_font') or None,
                       self.thumb.get('label_font') or None)

    def editable(self):
        """The layout that edits change: the batch's, or the selected thumbnail's own (made on first edit)."""
        job = self.jobs[self.current]
        if self.scope.get() == 'one':
            job['layout'] = layout_with(job.get('layout') or self.thumb['layout'])
            return job['layout']
        return self.thumb['layout']

    def change(self, kind=None, **spec):
        if self.quiet:
            return
        self.editable()[kind or self.kind.get()].update(spec)
        self.after_change()

    def after_change(self):
        self.show_controls(sync=False)
        self.draw_view()
        own = self.scope.get() == 'one'
        self.redraw([self.current] if own else [n for n, job in enumerate(self.jobs) if not job.get('layout')])

    def label_of(self, n):
        job = self.jobs[n]
        return job.get('label') or ('' if job.get('whole') else f"TEASER: {job['teaser_index']}")

    def type_text(self):
        """The text box edits the selected element: title and TEASER label per thumbnail, extra text by scope."""
        value, kind = self.words.get('1.0', 'end-1c'), self.kind.get()
        if self.quiet or kind == 'logo':
            return
        if kind == 'text':
            return self.change(kind='text', value=value)
        if kind == 'title':
            self.jobs[self.current]['title'] = ' '.join(value.split())
        else:
            self.jobs[self.current]['label'] = ' '.join(value.split())
        self.draw_view()
        self.redraw([self.current])

    def random_title(self):
        titles = load_titles(self.thumb['titles'])
        if titles:
            self.jobs[self.current]['title'] = random.choice(titles)
            self.kind.set('title')
            self.show_controls()
            self.redraw([self.current])

    def edit_text(self, event):
        if self.hit(event) in ('title', 'label', 'text'):
            self.words.focus_set()
            self.words.tag_add('sel', '1.0', 'end-1c')
            self.words.mark_set('insert', 'end-1c')

    def mirror(self):
        job = self.jobs[self.current]
        job['layout'] = mirrored(self.layout_of(self.current))
        self.scope.set('one')
        self.after_change()

    def reset(self):
        self.jobs[self.current].pop('layout', None)
        self.scope.set('all')
        self.after_change()

    # ---------- drawing ----------
    def select(self, n):
        self.current = n
        for i, (tile, _) in enumerate(self.tiles):
            tile.configure(relief='solid' if i == n else 'flat', background='#4fb3ff' if i == n else '#222')
        if self.jobs[n].get('layout'):
            self.scope.set('one')
        self.show_controls()

    def show_controls(self, sync=True):
        spec = self.layout_of(self.current)[self.kind.get()]
        if sync:
            self.quiet = True
            self.visible.set(spec['visible'])
            self.size.set(spec['size'])
            self.angle.set(spec['angle'])
            kind, job = self.kind.get(), self.jobs[self.current]
            self.words.configure(state='normal')
            self.words.delete('1.0', 'end')
            self.words.insert('1.0', {'title': job['title'], 'label': self.label_of(self.current), 'logo': '',
                                      'text': self.layout_of(self.current)['text'].get('value', '')}[kind])
            self.words_text.set({'title': 'Title (this thumbnail)', 'label': 'TEASER label (this thumbnail)',
                                 'logo': 'The logo has no text', 'text': 'Extra text (up to 3 lines)'}[kind])
            if kind == 'logo':
                self.words.configure(state='disabled')
            self.quiet = False
        self.size_text.set(f"Size  ×{spec['size']:.2f}")
        self.angle_text.set(f"Tilt  {spec['angle']:+.1f}°")
        self.reset_button.configure(state='normal' if self.jobs[self.current].get('layout') else 'disabled')
        if sync:
            self.draw_view()

    def draw_view(self):
        """Redraw the preview at most once per idle moment, so dragging stays smooth."""
        if not self.queued:
            self.queued = True
            self.win.after_idle(self._draw_view)

    def _draw_view(self):
        self.queued = False
        if not self.win.winfo_exists():
            return
        frame, self.boxes = self.picture(self.current, VIEW_W, VIEW_H)
        self.view_image = ImageTk.PhotoImage(frame)
        self.view.delete('all')
        self.view.create_image(0, 0, image=self.view_image, anchor='nw')
        box = self.boxes.get(self.kind.get())
        if box:
            self.view.create_rectangle(*box, outline='#4fb3ff', dash=(4, 3), width=2)

    def draw_tiles(self):
        """Draw one list tile per tick so a long batch fills in without freezing the window."""
        self.filling = bool(self.pending) and self.win.winfo_exists()
        if not self.filling:
            return
        n = self.pending.pop(0)
        self.tile_images[n] = ImageTk.PhotoImage(self.picture(n, TILE_W, TILE_H)[0])
        tile, caption = self.tiles[n]
        tile.configure(image=self.tile_images[n], width=TILE_W, height=TILE_H)
        job = self.jobs[n]
        caption.configure(text='  ·  '.join(filter(None, (job['name'], self.label_of(n), job.get('layout') and 'own layout'))))
        self.win.after(1, self.draw_tiles)

    def redraw(self, numbers):
        self.pending = [n for n in numbers if n not in self.pending] + self.pending
        if self.pending and not self.filling:
            self.filling = True
            self.win.after(150, self.draw_tiles)

    # ---------- dragging ----------
    def hit(self, event):
        return next((kind for kind in ('label', 'title', 'text', 'logo')
                     if kind in self.boxes and self.boxes[kind][0] <= event.x <= self.boxes[kind][2]
                     and self.boxes[kind][1] <= event.y <= self.boxes[kind][3]), None)

    def press(self, event):
        hit = self.hit(event)
        if hit:
            self.kind.set(hit)
            spec = self.layout_of(self.current)[hit]
            self.drag = (event.x, event.y, spec['x'], spec['y'])
            self.show_controls()

    def move(self, event):
        if not self.drag:
            return
        x0, y0, sx, sy = self.drag
        self.change(x=round(min(1.0, max(0.0, sx + (event.x - x0) / VIEW_W)), 4),
                    y=round(min(1.0, max(0.0, sy + (event.y - y0) / VIEW_H)), 4))

    # ---------- finish ----------
    def render(self):
        self.win.destroy()
        self.on_render()

    def back(self):
        self.win.destroy()
        self.on_back()
