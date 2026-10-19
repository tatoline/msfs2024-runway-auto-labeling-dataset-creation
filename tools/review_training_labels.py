"""Human-review every draft runway polygon before training-ground-truth export.

The GUI never connects to MSFS and never edits images or draft labels. It
autosaves accept/correct/exclude decisions tied to hashes of the exact inputs.
"""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

from PIL import Image, ImageEnhance


SCHEMA = 'runway_training_review_v1'
POLICY_V1 = ('Four corners in order: landing-threshold right, landing-threshold left, '
             'opposite-end left, opposite-end right, looking along the landing runway. '
             'Use runway edge/threshold intersections. Exclude shoulders, blast pads, '
             'taxiways and pavement before a displaced landing threshold. Exclude the '
             'image if any required boundary is not confidently visible; never guess.')
POLICY = ('Label the usable runway surface with the same rule in day and night images. '
          'Four corners in order: landing-threshold right, landing-threshold left, '
          'opposite-end left, opposite-end right, looking along the landing runway. '
          'Each corner is the intersection of the landing-threshold boundary and the '
          'runway side boundary. Follow the outer edge of the painted side stripe, or '
          'the corresponding usable pavement edge when no stripe is visible. Lights '
          'may help locate the boundary at night, but light centers, glow, threshold '
          'wing bars and lights outside the side boundary are not the runway boundary. '
          'Exclude shoulders, blast pads, taxiways and pavement before a displaced '
          'landing threshold. Exclude the image if any required boundary cannot be '
          'established confidently from the image and aligned projection; never guess.')
SUPPORTED_POLICIES = {POLICY_V1, POLICY}
FINAL = {'accepted_projection', 'accepted_corrected', 'excluded'}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def valid_polygon(points, width, height):
    if not isinstance(points, list) or len(points) != 4:
        return False
    try:
        pts = [(float(x), float(y)) for x, y in points]
    except (TypeError, ValueError):
        return False
    if not all(math.isfinite(v) for p in pts for v in p):
        return False
    if not all(0 <= x < width and 0 <= y < height for x, y in pts):
        return False
    cross = []
    for i in range(4):
        a, b, c = pts[i], pts[(i+1)%4], pts[(i+2)%4]
        cross.append((b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0]))
    area = abs(sum(pts[i][0]*pts[(i+1)%4][1]-pts[(i+1)%4][0]*pts[i][1]
                   for i in range(4)))/2
    return area >= 25 and (all(v > 0 for v in cross) or all(v < 0 for v in cross))


def interleaved_order(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[(row['icao'], row['hour'])].append(row)
    for values in groups.values():
        values.sort(key=lambda r: (float(r['ground_dist_m']), r['scenario_id']))
    order = []
    while groups:
        for key in sorted(list(groups)):
            values = groups[key]
            order.append(values.pop(len(values)//2))
            if not values:
                del groups[key]
    return order


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--package-dir', required=True)
    ap.add_argument('--output', required=True)
    ap.add_argument('--annotator', required=True, help='name recorded in provenance')
    args = ap.parse_args()
    package, output = Path(args.package_dir).resolve(), Path(args.output)
    manifest_path, report_path = package/'manifest.csv', package/'qa/label_report.json'
    import csv
    with manifest_path.open(newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    if not rows or len(rows) != len({r['scenario_id'] for r in rows}):
        ap.error('Package manifest must contain unique images')
    draft = {r['scenario_id']:r for r in json.loads(report_path.read_text())['images']}
    if set(draft) != {r['scenario_id'] for r in rows} or any(draft[s]['status'] != 'candidate' for s in draft):
        ap.error('Draft report must contain exactly one candidate per package image')
    active_policy = POLICY
    policy_hash = hashlib.sha256(active_policy.encode()).hexdigest()
    data = {'schema':SCHEMA, 'status':'IN_PROGRESS_NOT_GROUND_TRUTH', 'annotator':args.annotator,
            'created_utc':datetime.now(timezone.utc).isoformat(), 'updated_utc':None,
            'package':str(package), 'manifest_sha256':sha256(manifest_path),
            'draft_report_sha256':sha256(report_path), 'annotation_policy':active_policy,
            'annotation_policy_sha256':policy_hash, 'corner_order':['near_right','near_left','far_left','far_right'],
            'images':{}}
    if output.exists():
        saved=json.loads(output.read_text())
        saved_policy=saved.get('annotation_policy')
        saved_policy_hash=hashlib.sha256(saved_policy.encode()).hexdigest() if isinstance(saved_policy,str) else None
        if saved_policy not in SUPPORTED_POLICIES or saved.get('annotation_policy_sha256') != saved_policy_hash:
            ap.error('Existing review uses an unknown or altered annotation policy; use another --output')
        expected=(SCHEMA,args.annotator,data['manifest_sha256'],data['draft_report_sha256'],saved_policy_hash)
        actual=(saved.get('schema'),saved.get('annotator'),saved.get('manifest_sha256'),
                saved.get('draft_report_sha256'),saved.get('annotation_policy_sha256'))
        if actual != expected:
            ap.error('Existing review belongs to different inputs, policy or annotator; use another --output')
        active_policy=saved_policy;policy_hash=saved_policy_hash;data=saved
    output.parent.mkdir(parents=True, exist_ok=True)
    ordered=interleaved_order(rows)

    import tkinter as tk
    from tkinter import messagebox, simpledialog
    from PIL import ImageTk
    root=tk.Tk();root.title('Final runway ground-truth review');root.geometry('1400x900')
    header=tk.StringVar();status=tk.StringVar();notes=tk.StringVar()
    tk.Label(root,textvariable=header,font=('Segoe UI',12,'bold')).pack(pady=4)
    tk.Label(root,text='F: check full frame (required) | R: runway crop | A: accept + next | 1-4 then click: correct corner | X: exclude | S: skip\n'+active_policy,
             wraplength=1360,justify='left').pack(padx=10)
    canvas=tk.Canvas(root,bg='#222',highlightthickness=0);canvas.pack(fill='both',expand=True,padx=8,pady=5)
    tk.Label(root,textvariable=status,anchor='w').pack(fill='x',padx=10)
    bar=tk.Frame(root);bar.pack(fill='x',padx=8);tk.Label(bar,text='Notes:').pack(side='left');tk.Entry(bar,textvariable=notes).pack(side='left',fill='x',expand=True)
    controls=tk.Frame(root);controls.pack(pady=6)
    state={'index':0,'image':None,'display':None,'scale':1.,'ox':0.,'oy':0.,'active':None,'pan':None,'enhanced':False}

    def sid(): return ordered[state['index']]['scenario_id']
    def current(): return data['images'][sid()]
    def save():
        if state['image'] is not None: current()['notes']=notes.get()
        data['updated_utc']=datetime.now(timezone.utc).isoformat()
        data['status']='COMPLETE_READY_TO_EXPORT' if len(data['images'])==len(rows) and all(r.get('decision') in FINAL for r in data['images'].values()) else 'IN_PROGRESS_NOT_GROUND_TRUTH'
        temp=output.with_name(output.name+'.tmp');temp.write_text(json.dumps(data,indent=2),encoding='utf-8');temp.replace(output)

    def render():
        if state['image'] is None:return
        canvas.delete('all');im=state['display'];s,ox,oy=state['scale'],state['ox'],state['oy'];cw,ch=canvas.winfo_width(),canvas.winfo_height()
        left,top=max(0,math.floor(-ox/s)),max(0,math.floor(-oy/s));right,bottom=min(im.width,math.ceil((cw-ox)/s)),min(im.height,math.ceil((ch-oy)/s))
        if right>left and bottom>top:
            crop=im.crop((left,top,right,bottom)).resize((max(1,round((right-left)*s)),max(1,round((bottom-top)*s))),Image.Resampling.NEAREST if s>=2 else Image.Resampling.LANCZOS)
            state['tk']=ImageTk.PhotoImage(crop);canvas.create_image(ox+left*s,oy+top*s,image=state['tk'],anchor='nw')
        rec=current();pts=rec['corners_px']
        if all(p is not None for p in pts):
            shown=[(ox+x*s,oy+y*s) for x,y in pts];canvas.create_polygon(*sum(([x,y] for x,y in shown),[]),outline='#00ff40',fill='',width=2)
            for i,(x,y) in enumerate(shown):
                color='#ff4545' if state['active']==i else '#00ffff';canvas.create_oval(x-5,y-5,x+5,y+5,outline=color,width=2);canvas.create_text(x+11,y-11,text=str(i+1),fill='yellow',font=('Segoe UI',13,'bold'))
        counts=defaultdict(int)
        for item in data['images'].values():counts[item.get('decision','unreviewed')]+=1
        action='Accept, correct a corner, or exclude. Green must follow the visible target boundary.' if state['active'] is None else f'Click corrected corner {state["active"]+1}'
        status.set(f'{action} | full-frame checked {rec.get("full_frame_checked",False)} | final {sum(counts[x] for x in FINAL)}/{len(rows)} | accepted {counts["accepted_projection"]+counts["accepted_corrected"]} | excluded {counts["excluded"]} | {s:.2f}x | enhanced {state["enhanced"]}')

    def fit(full=False):
        im=state['image'];pts=current()['corners_px']
        if full:x0,y0,x1,y1=0,0,im.width,im.height
        else:
            xs=[p[0] for p in pts];ys=[p[1] for p in pts];x0,y0,x1,y1=max(0,min(xs)-150),max(0,min(ys)-110),min(im.width,max(xs)+150),min(im.height,max(ys)+110)
        scale=min(canvas.winfo_width()/(x1-x0),canvas.winfo_height()/(y1-y0))*.94
        state.update(scale=scale,ox=canvas.winfo_width()/2-(x0+x1)*scale/2,oy=canvas.winfo_height()/2-(y0+y1)*scale/2);render()

    def load(index):
        if state['image'] is not None:save()
        state['index']=index%len(ordered);row=ordered[state['index']];path=(package/'images'/f'{row["scenario_id"]}.png').resolve()
        if not path.is_relative_to(package):raise ValueError('Unsafe image path')
        digest=sha256(path);previous=data['images'].get(row['scenario_id'])
        if previous and previous['image_sha256']!=digest:raise ValueError('Image changed since review began')
        with Image.open(path) as opened:state['image']=opened.convert('RGB')
        proposed=[[round(float(x),3),round(float(y),3)] for x,y,*_ in draft[row['scenario_id']]['corners_px']]
        rec=data['images'].setdefault(row['scenario_id'],{'scenario_id':row['scenario_id'],'icao':row['icao'],'hour':int(row['hour']),
             'image_file':f'images/{row["scenario_id"]}.png','image_sha256':digest,'image_size':list(state['image'].size),
             'initial_corners_px':proposed,'corners_px':[p[:] for p in proposed],'decision':'unreviewed','exclusion_reason':None,'notes':'','reviewed_utc':None,
             'full_frame_checked':False,'contrast_used':False})
        notes.set(rec.get('notes',''));state.update(active=None,enhanced=False,display=state['image'])
        header.set(f'{state["index"]+1}/{len(rows)}  {row["icao"]}  {int(row["hour"]):02d} UTC  {float(row["ground_dist_m"]):.0f} m  {row["scenario_id"]}  [{rec["decision"]}]');fit()

    def accept():
        rec=current();w,h=rec['image_size']
        if rec.get('full_frame_checked') is not True:messagebox.showerror('Full-frame check required','Press F and inspect the entire screenshot before accepting.');return
        if not valid_polygon(rec['corners_px'],w,h):messagebox.showerror('Invalid polygon','Corners must form a convex in-frame quadrilateral.');return
        changed=rec['corners_px']!=rec['initial_corners_px'];rec.update(decision='accepted_corrected' if changed else 'accepted_projection',exclusion_reason=None,reviewed_utc=datetime.now(timezone.utc).isoformat());save();load(state['index']+1)

    def exclude():
        reason=simpledialog.askstring('Exclude image','Why can this image not provide reliable runway ground truth?\nExamples: boundary occluded, too dark, UI contamination, wrong target',parent=root)
        if not reason or not reason.strip():return
        current().update(decision='excluded',exclusion_reason=reason.strip(),reviewed_utc=datetime.now(timezone.utc).isoformat());save();load(state['index']+1)

    def click(event):
        if state['active'] is None:return
        x,y=(event.x-state['ox'])/state['scale'],(event.y-state['oy'])/state['scale'];w,h=current()['image_size']
        if 0<=x<w and 0<=y<h:
            current()['corners_px'][state['active']]=[round(x,3),round(y,3)];current()['decision']='unreviewed';state['active']=None;save();render()

    def zoom(event):
        old=state['scale'];new=max(.2,min(20.,old*(1.2 if event.delta>0 else 1/1.2)));state.update(scale=new,ox=event.x-(event.x-state['ox'])*new/old,oy=event.y-(event.y-state['oy'])*new/old);render()
    def pan_start(e):state['pan']=(e.x,e.y,state['ox'],state['oy'])
    def pan(e):
        if state['pan']:
            x,y,ox,oy=state['pan'];state.update(ox=ox+e.x-x,oy=oy+e.y-y);render()
    def enhance():
        state['enhanced']=not state['enhanced'];current()['contrast_used']=current().get('contrast_used',False) or state['enhanced'];state['display']=ImageEnhance.Contrast(state['image']).enhance(2.0) if state['enhanced'] else state['image'];save();render()
    def show_full():current()['full_frame_checked']=True;save();fit(True)
    def key(e):
        if isinstance(root.focus_get(),tk.Entry):return
        c=e.char.lower()
        if c in '1234':state['active']=int(c)-1;render()
        elif c=='a':accept()
        elif c=='x':exclude()
        elif c=='s':load(state['index']+1)
        elif c=='r':fit()
        elif c=='f':show_full()
        elif c=='g':enhance()
    def close():save();root.destroy()
    for title,cmd in [('Previous',lambda:load(state['index']-1)),('Accept + next (A)',accept),('Exclude (X)',exclude),('Skip (S)',lambda:load(state['index']+1)),('Runway crop (R)',fit),('Check full frame (F)',show_full),('Contrast (G)',enhance),('Save and close',close)]:tk.Button(controls,text=title,command=cmd).pack(side='left',padx=3)
    canvas.bind('<Button-1>',click);canvas.bind('<MouseWheel>',zoom);canvas.bind('<Button-2>',pan_start);canvas.bind('<B2-Motion>',pan);canvas.bind('<Configure>',lambda e:render());root.bind('<Key>',key);root.protocol('WM_DELETE_WINDOW',close)
    root.report_callback_exception=lambda exc,value,tb:messagebox.showerror('Review error',f'{value}\nSaved decisions are preserved.')
    root.update_idletasks();load(next((i for i,r in enumerate(ordered) if data['images'].get(r['scenario_id'],{}).get('decision') not in FINAL),0));root.mainloop()


if __name__=='__main__':main()
