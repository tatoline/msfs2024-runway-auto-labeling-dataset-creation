"""Collect reference corner measurements on saved images; never connects to MSFS.

Measurements are autosaved and can be resumed. They are not label approval.
Use the threshold bar / runway edge intersections, not shoulders or blast pads.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

from PIL import Image


CORNER_NAMES = ['near right', 'near left', 'far left', 'far right']


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dataset-dir', required=True)
    ap.add_argument('--references', help='defaults to dataset-dir/labeling/references.json')
    ap.add_argument('--output', help='defaults to dataset-dir/labeling/manual_corners.json')
    args = ap.parse_args()
    dataset = Path(args.dataset_dir).resolve()
    refs_path = Path(args.references) if args.references else dataset/'labeling/references.json'
    output = Path(args.output) if args.output else dataset/'labeling/manual_corners.json'
    refs = json.loads(refs_path.read_text(encoding='utf-8'))['references']
    if not refs or len({r['scenario_id'] for r in refs}) != len(refs):
        ap.error('References must contain unique scenario IDs')
    manifest_hash = hashlib.sha256((dataset/'manifest.csv').read_bytes()).hexdigest()
    data = {'schema': 'runway_reference_measurements_v1', 'status': 'MEASUREMENTS_NOT_LABEL_APPROVAL',
            'manifest_sha256': manifest_hash, 'dataset': str(dataset),
            'corner_order': CORNER_NAMES, 'images': {}}
    if output.exists():
        saved = json.loads(output.read_text(encoding='utf-8'))
        if saved.get('schema') != data['schema'] or saved.get('manifest_sha256') != manifest_hash:
            ap.error('Existing output is not a matching measurement session; use a different --output')
        data = saved
    output.parent.mkdir(parents=True, exist_ok=True)
    draft_path = dataset/'labeling/labels_draft/label_report.json'
    drafts = {r['scenario_id']: r for r in json.loads(draft_path.read_text(encoding='utf-8'))['images']} if draft_path.exists() else {}

    import tkinter as tk
    from tkinter import messagebox
    from PIL import ImageTk

    root = tk.Tk()
    root.title('Runway reference corners - saved images only')
    root.geometry('1320x850')
    header = tk.StringVar()
    status = tk.StringVar()
    tk.Label(root, textvariable=header, font=('Segoe UI', 12, 'bold')).pack(pady=5)
    tk.Label(root, text='Click in order: 1 near RIGHT, 2 near LEFT, 3 far LEFT, 4 far RIGHT, looking down the landing runway.\n'
             'Use the landing threshold bar and runway edges; exclude displaced approach pavement, shoulders and blast pads.\n'
             'Wheel: zoom at cursor | Middle-drag: pan | Right-click: undo | Keys 1-4: correct a corner | R: fit runway | F: full image').pack()
    canvas = tk.Canvas(root, bg='#222222', highlightthickness=0)
    canvas.pack(fill='both', expand=True, padx=8, pady=5)
    tk.Label(root, textvariable=status, anchor='w').pack(fill='x', padx=10)
    notes = tk.StringVar()
    bar = tk.Frame(root)
    bar.pack(fill='x', padx=8)
    tk.Label(bar, text='Notes:').pack(side='left')
    tk.Entry(bar, textvariable=notes).pack(side='left', fill='x', expand=True)
    controls = tk.Frame(root)
    controls.pack(pady=7)
    state = {'index': 0, 'image': None, 'scale': 1., 'ox': 0., 'oy': 0., 'active': 0, 'pan': None}

    def current():
        return data['images'][refs[state['index']]['scenario_id']]

    def save():
        if state['image'] is not None:
            rec = current()
            rec['notes'] = notes.get()
            if rec.get('status') != 'uncertain_or_occluded':
                rec['status'] = 'four_corners_measured' if all(p is not None for p in rec['corners_px']) else 'incomplete'
        temp = output.with_name(output.name+'.tmp')
        temp.write_text(json.dumps(data, indent=2), encoding='utf-8')
        temp.replace(output)

    def render():
        im = state['image']
        if im is None:
            return
        canvas.delete('all')
        s, ox, oy = state['scale'], state['ox'], state['oy']
        cw, ch = canvas.winfo_width(), canvas.winfo_height()
        left, top = max(0, math.floor(-ox/s)), max(0, math.floor(-oy/s))
        right, bottom = min(im.width, math.ceil((cw-ox)/s)), min(im.height, math.ceil((ch-oy)/s))
        if right > left and bottom > top:
            crop = im.crop((left, top, right, bottom))
            crop = crop.resize((max(1, round((right-left)*s)), max(1, round((bottom-top)*s))), Image.Resampling.NEAREST if s >= 2 else Image.Resampling.LANCZOS)
            state['tk_image'] = ImageTk.PhotoImage(crop)
            canvas.create_image(ox+left*s, oy+top*s, image=state['tk_image'], anchor='nw')
        for i, point in enumerate(current()['corners_px']):
            if point is None:
                continue
            x, y = ox+point[0]*s, oy+point[1]*s
            canvas.create_oval(x-4, y-4, x+4, y+4, outline='#ff4545', width=2)
            canvas.create_text(x+10, y-10, text=str(i+1), fill='yellow', font=('Segoe UI', 13, 'bold'))
        active = state['active']
        action = f'Next click: {active+1} {CORNER_NAMES[active]}' if active < 4 else 'Four corners saved. Use 1-4 to adjust, or Next.'
        complete = sum(r.get('status') == 'four_corners_measured' for r in data['images'].values())
        status.set(f'{action} | {complete}/{len(refs)} measured | {s:.2f}x | Autosave: {output.name}')

    def fit(full=False):
        im = state['image']
        x0, y0, x1, y1 = 0, 0, im.width, im.height
        draft = drafts.get(refs[state['index']]['scenario_id'], {})
        bb = draft.get('bbox_px')
        if bb and not full:
            x0, y0, x1, y1 = max(0, bb[0]-140), max(0, bb[1]-100), min(im.width, bb[2]+140), min(im.height, bb[3]+100)
        s = min(canvas.winfo_width()/(x1-x0), canvas.winfo_height()/(y1-y0))*.94
        state.update(scale=s, ox=canvas.winfo_width()/2-(x0+x1)*s/2, oy=canvas.winfo_height()/2-(y0+y1)*s/2)
        render()

    def load(index):
        if state['image'] is not None:
            save()
        state['index'] = index % len(refs)
        ref = refs[state['index']]
        path = (dataset/ref['image_file']).resolve()
        if not path.is_relative_to(dataset):
            raise ValueError('Image path outside dataset')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        previous = data['images'].get(ref['scenario_id'])
        if previous and previous.get('image_sha256') != digest:
            raise ValueError('Reference image changed since measurement; use a fresh --output')
        with Image.open(path) as opened:
            state['image'] = opened.convert('RGB')
        rec = data['images'].setdefault(ref['scenario_id'], {
            'scenario_id': ref['scenario_id'], 'icao': ref['icao'], 'distance_group': ref['distance_group'],
            'image_file': ref['image_file'], 'image_sha256': digest,
            'image_size': list(state['image'].size), 'corners_px': [None]*4, 'status': 'incomplete', 'notes': ''})
        notes.set(rec.get('notes', ''))
        state['active'] = next((i for i, p in enumerate(rec['corners_px']) if p is None), 4)
        header.set(f'{state["index"]+1}/{len(refs)}: {ref["icao"]} - {ref["distance_group"]} - {ref["scenario_id"]}')
        fit()

    def click(event):
        i = state['active']
        if i >= 4:
            return
        x, y = (event.x-state['ox'])/state['scale'], (event.y-state['oy'])/state['scale']
        if not (0 <= x < state['image'].width and 0 <= y < state['image'].height):
            return
        rec = current()
        rec['corners_px'][i] = [round(x, 3), round(y, 3)]
        rec['status'] = 'incomplete'
        state['active'] = next((j for j, p in enumerate(rec['corners_px']) if p is None), 4)
        save()
        render()

    def undo(event=None):
        rec = current()
        populated = [i for i, p in enumerate(rec['corners_px']) if p is not None]
        if populated:
            i = populated[-1]
            rec['corners_px'][i] = None
            rec['status'] = 'incomplete'
            state['active'] = i
            save()
            render()

    def uncertain():
        current()['status'] = 'uncertain_or_occluded'
        save()
        load(state['index']+1)

    def zoom(event):
        old = state['scale']
        new = max(.2, min(16., old*(1.2 if event.delta > 0 else 1/1.2)))
        state.update(scale=new, ox=event.x-(event.x-state['ox'])*new/old, oy=event.y-(event.y-state['oy'])*new/old)
        render()

    def pan_start(event):
        state['pan'] = (event.x, event.y, state['ox'], state['oy'])

    def pan(event):
        if state['pan']:
            x, y, ox, oy = state['pan']
            state.update(ox=ox+event.x-x, oy=oy+event.y-y)
            render()

    def key(event):
        if isinstance(root.focus_get(), tk.Entry):
            return
        if event.char in ('1', '2', '3', '4'):
            state['active'] = int(event.char)-1
            render()
        elif event.char.lower() == 'r':
            fit()
        elif event.char.lower() == 'f':
            fit(True)

    def close():
        save()
        root.destroy()

    for title, command in [('Previous', lambda: load(state['index']-1)), ('Undo point', undo),
                           ('Fit runway', fit), ('Full image', lambda: fit(True)),
                           ('Uncertain / occluded: skip', uncertain), ('Next', lambda: load(state['index']+1)),
                           ('Save and close', close)]:
        tk.Button(controls, text=title, command=command).pack(side='left', padx=4)
    canvas.bind('<Button-1>', click)
    canvas.bind('<Button-3>', undo)
    canvas.bind('<MouseWheel>', zoom)
    canvas.bind('<Button-2>', pan_start)
    canvas.bind('<B2-Motion>', pan)
    canvas.bind('<Configure>', lambda e: render())
    root.bind('<Key>', key)
    root.protocol('WM_DELETE_WINDOW', close)

    def report_error(exc, value, tb):
        messagebox.showerror('Review error', f'{value}\nMeasurements already saved are preserved.')
    root.report_callback_exception = report_error
    root.update_idletasks()
    load(next((i for i, r in enumerate(refs) if data['images'].get(r['scenario_id'], {}).get('status') not in ('four_corners_measured', 'uncertain_or_occluded')), 0))
    root.mainloop()


if __name__ == '__main__':
    main()
