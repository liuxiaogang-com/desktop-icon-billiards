"""Local cue-ball artwork and its small appearance window."""
import os
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

from PIL import Image, ImageDraw, ImageOps, ImageTk
import win32api
import win32con

from .art import white_art
from .i18n import t
from .panel import ACCENT, BG, CARD, MUTED, TEXT, Pill, ui_font


def read_source(path):
    """Decode and orient once, with a bounded editing resolution."""
    path = Path(path)
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError(t('white_ball.error.too_large'))
    with Image.open(path) as source:
        if source.format not in ('JPEG', 'PNG', 'WEBP'):
            raise ValueError(t('white_ball.error.format'))
        if source.width * source.height > 25_000_000:
            raise ValueError(t('white_ball.error.pixels'))
        source = ImageOps.exif_transpose(source)
        source.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
        rgba = source.convert('RGBA')
    result = Image.new('RGB', rgba.size, (246, 249, 250))
    result.paste(rgba, mask=rgba.getchannel('A'))
    return result


def crop_box(image, zoom=1., center=None):
    """The shorter side fills the circle; clamp panning so no empty edge appears."""
    side = min(image.size) / max(1., min(4., zoom))
    cx, cy = center if center is not None else (image.width / 2, image.height / 2)
    cx = max(side / 2, min(image.width - side / 2, cx))
    cy = max(side / 2, min(image.height - side / 2, cy))
    return cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2


def crop_texture(image, zoom=1., center=None):
    return image.resize((256, 256), Image.Resampling.LANCZOS, box=crop_box(image, zoom, center))


def read_texture(path):
    return crop_texture(read_source(path))


def save_texture(path, texture):
    """Replace only the processed cache; preserve the previous image on failure."""
    path = Path(path)
    if texture is None:
        path.unlink(missing_ok=True)
        return
    temporary = path.with_suffix('.tmp')
    try:
        texture.save(temporary, format='PNG')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class CropDialog:
    def __init__(self, parent, source, on_apply):
        self.source = source
        self.on_apply = on_apply
        self.center = (source.width / 2, source.height / 2)
        self.zoom = 1.
        self.drag = None
        self.pending = None
        self.window = dialog = tk.Toplevel(parent)
        dialog.withdraw()
        dialog.title(t('white_ball.crop_title'))
        dialog.configure(bg=BG)
        dialog.transient(parent)
        dialog.resizable(False, False)
        monitor = win32api.MonitorFromPoint((parent.winfo_rootx(), parent.winfo_rooty()),
                                           win32con.MONITOR_DEFAULTTONEAREST)
        left, top, right, bottom = win32api.GetMonitorInfo(monitor)['Work']
        self.side = max(96, min(320, right - left - 80, bottom - top - 230))
        body = tk.Frame(dialog, bg=BG, padx=20, pady=16)
        body.pack()
        tk.Label(body, text=t('white_ball.crop_hint'), font=ui_font(9),
                 bg=BG, fg=MUTED).pack(pady=(0, 12))
        self.canvas = tk.Canvas(body, width=self.side, height=self.side, bg=CARD,
                                highlightthickness=0, cursor='fleur')
        self.canvas.pack()
        self.image_item = self.canvas.create_image(0, 0, anchor='nw')
        self.canvas.bind('<ButtonPress-1>', self.start_drag)
        self.canvas.bind('<B1-Motion>', self.move)
        self.canvas.bind('<ButtonRelease-1>', lambda event: setattr(self, 'drag', None))
        self.canvas.bind('<MouseWheel>', self.wheel)
        self.slider = tk.Scale(body, from_=1, to=4, resolution=.01, orient='horizontal',
                               command=self.scale, bg=BG, fg=TEXT, troughcolor=CARD,
                               highlightthickness=0, activebackground=ACCENT, showvalue=False)
        self.slider.set(1)
        self.slider.pack(fill='x', pady=(8, 0))
        self.zoom_label = tk.Label(body, text='100%', font=ui_font(9), bg=BG, fg=MUTED)
        self.zoom_label.pack()
        actions = tk.Frame(body, bg=BG)
        actions.pack(fill='x', pady=(12, 0))
        Pill(actions, t('white_ball.cancel'), self.close, 'soft', padx=16, pady=8).pack(side='left')
        Pill(actions, t('white_ball.apply'), self.confirm, 'primary', bold=True,
             padx=16, pady=8).pack(side='right')
        dialog.bind('<Escape>', lambda event: self.close())
        dialog.protocol('WM_DELETE_WINDOW', self.close)
        self.render()
        dialog.update_idletasks()
        x = left + max(0, (right - left - dialog.winfo_reqwidth()) // 2)
        y = top + max(0, (bottom - top - dialog.winfo_reqheight()) // 2)
        dialog.geometry(f'+{x}+{y}')
        dialog.deiconify()
        dialog.grab_set()

    def render(self):
        self.pending = None
        box = crop_box(self.source, self.zoom, self.center)
        self.center = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        frame = self.source.resize((self.side, self.side), Image.Resampling.LANCZOS, box=box)
        mask = Image.new('L', frame.size)
        ImageDraw.Draw(mask).ellipse((1, 1, self.side - 2, self.side - 2), fill=255)
        dim = Image.blend(frame, Image.new('RGB', frame.size, BG), .8)
        frame = Image.composite(frame, dim, mask)
        ImageDraw.Draw(frame).ellipse((1, 1, self.side - 2, self.side - 2), outline=ACCENT, width=2)
        self.photo = ImageTk.PhotoImage(frame, master=self.window)
        self.canvas.itemconfigure(self.image_item, image=self.photo)
        self.zoom_label.configure(text=f'{self.zoom:.0%}')

    def redraw(self):
        if self.pending is None:
            self.pending = self.window.after(16, self.render)

    def start_drag(self, event):
        self.drag = (event.x, event.y)

    def move(self, event):
        if self.drag is None:
            return
        factor = min(self.source.size) / self.zoom / self.side
        cx, cy = self.center
        box = crop_box(self.source, self.zoom,
                       (cx - (event.x - self.drag[0]) * factor, cy - (event.y - self.drag[1]) * factor))
        self.center = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        self.drag = (event.x, event.y)
        self.redraw()

    def scale(self, value):
        self.zoom = float(value)
        self.redraw()

    def wheel(self, event):
        self.slider.set(max(1., min(4., self.zoom + (.1 if event.delta > 0 else -.1))))
        return 'break'

    def confirm(self):
        try:
            self.on_apply(crop_texture(self.source, self.zoom, self.center))
        except OSError as error:
            messagebox.showwarning(t('white_ball.title'), t('white_ball.error.save_failed', error=error),
                                   parent=self.window)
            return
        self.close()

    def close(self):
        if self.pending is not None:
            self.window.after_cancel(self.pending)
            self.pending = None
        self.window.grab_release()
        self.window.destroy()


class WhiteBallDialog:
    def __init__(self, root, path, texture, on_change):
        self.path = path
        self.texture = texture
        self.on_change = on_change
        self.window = dialog = tk.Toplevel(root)
        dialog.title(t('white_ball.title'))
        dialog.configure(bg=BG)
        dialog.resizable(False, False)
        # A transient child would disappear when the stealth toolbar withdraws.
        body = tk.Frame(dialog, bg=BG, padx=24, pady=20)
        body.pack(fill='both', expand=True)
        tk.Label(body, text=t('white_ball.title'), font=ui_font(14, bold=True), fg=TEXT, bg=BG).pack()
        tk.Label(body, text=t('white_ball.intro'),
                 font=ui_font(9), fg=MUTED, bg=BG).pack(anchor='w', pady=(5, 14))
        self.preview = tk.Label(body, bg=CARD, padx=70, pady=18)
        self.preview.pack(fill='x')
        self.status = tk.Label(body, font=ui_font(9), fg=ACCENT, bg=BG)
        self.status.pack(pady=(10, 14))
        actions = tk.Frame(body, bg=BG)
        actions.pack(fill='x')
        Pill(actions, t('white_ball.choose'), self.choose, 'primary', bold=True,
             padx=16, pady=8).pack(side='left')
        self.reset_button = Pill(actions, t('white_ball.reset'), self.reset, 'soft', padx=16, pady=8)
        self.reset_button.pack(side='right')
        tk.Label(body, text=t('white_ball.hint'),
                 font=ui_font(8), fg=MUTED, bg=BG).pack(pady=(12, 0))
        dialog.bind('<Escape>', lambda event: dialog.destroy())
        self.refresh()
        dialog.update_idletasks()
        x = max(0, (dialog.winfo_screenwidth() - dialog.winfo_reqwidth()) // 2)
        y = max(0, (dialog.winfo_screenheight() - dialog.winfo_reqheight()) // 2)
        dialog.geometry(f'+{x}+{y}')

    def refresh(self):
        self.photo = ImageTk.PhotoImage(white_art(46, shaded=True, texture=self.texture), master=self.window)
        self.preview.configure(image=self.photo)
        self.status.configure(text=t('white_ball.status_custom') if self.texture is not None
                              else t('white_ball.status_default'))
        self.reset_button.set(enabled=self.texture is not None or self.path.exists())

    def apply(self, texture):
        save_texture(self.path, texture)
        self.on_change(texture)
        self.texture = texture
        self.refresh()

    def choose(self):
        path = filedialog.askopenfilename(parent=self.window, title=t('white_ball.picker_title'),
                                         filetypes=[(t('white_ball.picker_type'), '*.jpg *.jpeg *.png *.webp')])
        if not path:
            return
        try:
            CropDialog(self.window, read_source(path), self.apply)
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            messagebox.showwarning(t('white_ball.title'), t('white_ball.error.load_failed', error=error),
                                   parent=self.window)

    def reset(self):
        try:
            self.apply(None)
        except OSError as error:
            messagebox.showwarning(t('white_ball.title'), t('white_ball.error.reset_failed', error=error),
                                   parent=self.window)
