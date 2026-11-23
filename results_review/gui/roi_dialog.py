"""Scientific ROI settings and source-photo geometry review."""
import tkinter as tk
from tkinter import ttk, messagebox
from types import SimpleNamespace


def ask_settings(parent, cfg):
    from src.spotting_roi import settings_from_config, LEGACY_METHOD
    current = settings_from_config(cfg or {}, legacy_if_missing=True)
    win = tk.Toplevel(parent)
    win.title("Measurement ROI")
    win.transient(parent.winfo_toplevel())
    body = ttk.Frame(win, padding=16); body.pack(fill="both", expand=True)
    ttk.Label(body, text=f"Current method: {current.method}").grid(row=0,column=0,columnspan=2,sticky="w")
    ttk.Label(body, text="Inward margin (% of safe spot radius):").grid(row=1,column=0,sticky="w",pady=8)
    margin = tk.StringVar(value=f"{15 if current.method == LEGACY_METHOD else current.inward_margin*100:g}")
    ttk.Spinbox(body, from_=0,to=95,increment=1,textvariable=margin,width=8).grid(row=1,column=1)
    ttk.Label(body,text="Minimum interior pixels:").grid(row=2,column=0,sticky="w")
    pixels = tk.StringVar(value=str(current.min_pixels))
    ttk.Spinbox(body,from_=1,to=10000,textvariable=pixels,width=8).grid(row=2,column=1)
    ttk.Label(body, text="Recalculates from original photographs with centered-interior-v1.\n"
        "Applies the same ROI and background rules to controls and experimental spots.\n"
        "All conditions use these settings; other candidates recalculate when opened.\n"
        "Diagnostic raw values are retained. Save review to keep these settings.\n"
        "Original candidate rankings remain the historical run's rankings.",
        wraplength=550).grid(row=3,column=0,columnspan=2,sticky="w",pady=12)
    result = []
    def accept():
        from src.spotting_roi import ROISettings
        try:
            settings = ROISettings(inward_margin=float(margin.get())/100,
                                   min_pixels=int(pixels.get()))
        except (ValueError, TypeError) as exc:
            messagebox.showerror("Measurement ROI",str(exc),parent=win); return
        result.append(settings); win.destroy()
    ttk.Button(body,text="Recalculate with interior ROI",command=accept).grid(row=4,column=0,sticky="w")
    ttk.Button(body,text="Cancel",command=win.destroy).grid(row=4,column=1,sticky="e")
    win.grab_set(); win.wait_window()
    return result[0] if result else None


def show_overlays(parent, frame):
    from data_review.spots import Detected
    from data_review.gui.plate import PlateView, Magnifier, open_preview, open_full
    win = tk.Toplevel(parent); win.title("Detected boundary and actual measurement ROI")
    win.geometry("1200x820")
    win.rowconfigure(1,weight=1); win.columnconfigure(0,weight=1)
    head = ttk.Frame(win,padding=8); head.grid(row=0,column=0,columnspan=2,sticky="ew")
    label = ttk.Label(head); label.pack(side="left",expand=True,fill="x")
    detail = tk.StringVar()
    ttk.Label(win,textvariable=detail,wraplength=1150).grid(row=2,column=0,columnspan=2,sticky="w",padx=8,pady=8)
    mag = Magnifier(win, size=360); mag.grid(row=1,column=1,sticky="n")
    state = {}
    def hover(cell):
        if not state:
            return
        found=state['found']; pixels=state['pixels']
        mag.show(pixels,found,cell,{},state['cells'])
        detail.set(f"row {cell[0]+1}, column {cell[1]+1}: "
                   f"{found.quality.get(cell) or 'usable interior'}; "
                   f"net mean grey {found.net[cell]:.5g}; radius {found.radius[cell]:.3f} px")
    plate = PlateView(win,on_hover=hover,on_click=hover,on_menu=lambda *a:None)
    plate.grid(row=1,column=0,sticky="nsew")
    def load(index):
        pd=frame.plates[index]; p=pd.provenance or {}
        import numpy as np
        if not np.isfinite(pd.centers).any():
            label.configure(text=f"{pd.ref.path.name} | measurement unavailable")
            detail.set(p.get("measurement_error", "No usable ROI geometry"))
            plate.set_overlay(None, {}, {}, "", False)
            plate.show_photo(None, "Measurement unavailable; see the reason below")
            mag.clear()
            state.clear()
            return
        found=Detected(pd.centers,pd.plate_center,pd.plate_radius,
                       method=p.get('settings',{}).get('method','fixed-oval-v23'))
        cells={}
        import numpy as np
        for r,c in np.ndindex(pd.centers.shape[:2]):
            key=(r,c); found.radius[key]=pd.radius; found.net[key]=pd.net[key]
            if pd.rim[key]: found.rim.add(key)
            if 'boundary_radii' in p: found.boundary[key]=p['boundary_radii'][key]
            if 'quality' in p: found.quality[key]=p['quality'][key]
            cells[key]=SimpleNamespace(is_control=False)
        pixels=open_preview(pd.ref.path); pixels.full=open_full(pd.ref.path)
        state.update(found=found,pixels=pixels,cells=cells)
        label.configure(text=f"{pd.ref.path.name} | {found.method} | "
            f"margin {p.get('settings',{}).get('inward_margin',0):.0%}")
        plate.show_photo(pixels); plate.set_overlay(found,cells,{},"",False)
        hover((0,0))
    for i,p in enumerate(frame.plates):
        ttk.Button(head,text=f"Photo {i+1}",command=lambda i=i:load(i)).pack(side="right")
    if frame.plates: load(0)
    else: label.configure(text="ROI geometry unavailable; rebuild this candidate first.")
