"""Fast visual scoring for enhanced-runway-light pilot captures.

Ratings use the project convention:
  1 = extremely overexposed, 5 = acceptable, 10 = perfect.

Select a completed pilot with --pilot-dir. Results
are saved after every action, so the review can be safely closed and resumed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageTk


HERE = Path(__file__).resolve().parent
WORK_ROOT = HERE.parent / "runs"
FINAL_DECISIONS = {"rated", "unrated_not_visible"}
SCHEMA = "odm-enhanced-light-human-rating/v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(path)


def completed_pilot(path: Path) -> bool:
    run_path = path / "capture_run.json"
    batch_path = path / "captured_batch.json"
    image_dir = path / "images"
    if not (run_path.is_file() and batch_path.is_file() and image_dir.is_dir()):
        return False
    try:
        run = read_json(run_path)
        batch = read_json(batch_path)
    except (OSError, ValueError):
        return False
    return (run.get("status") == "CAPTURE_COMPLETE_AWAITING_LABEL_VALIDATION"
            and len(list(image_dir.glob("*.png"))) == len(batch))


def latest_pilot() -> Path:
    candidates = [
        path for path in WORK_ROOT.glob("pilot*_rain_five_airports_*")
        if path.is_dir() and completed_pilot(path)
    ]
    if not candidates:
        raise FileNotFoundError(
            f"No completed enhanced-light pilot was found under {WORK_ROOT}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def profile_from_run(run: dict, pilot: Path) -> str:
    notes = str(run.get("arguments", {}).get("notes", ""))
    match = re.search(r"(?:^|;)\s*profile=([^;]+)", notes)
    return match.group(1).strip() if match else pilot.name


def ordered_scenarios(pilot: Path) -> list[dict]:
    batch = read_json(pilot / "captured_batch.json")
    return sorted(batch, key=lambda row: (
        str(row.get("icao", "")), float(row.get("gd", 0)), str(row.get("sid", ""))))


def new_review(pilot: Path, scenarios: list[dict], annotator: str) -> dict:
    run_path = pilot / "capture_run.json"
    run = read_json(run_path)
    return {
        "schema": SCHEMA,
        "status": "IN_PROGRESS",
        "annotator": annotator,
        "created_utc": utc_now(),
        "updated_utc": utc_now(),
        "source_capture": str(pilot),
        "source_capture_run_sha256": sha256(run_path),
        "light_profile": profile_from_run(run, pilot),
        "rating_definition": {
            "1": "extremely overexposed",
            "5": "acceptable",
            "10": "perfect",
        },
        "scenario_order": [row["sid"] for row in scenarios],
        "images": {},
    }


def validate_review(review: dict, pilot: Path, annotator: str) -> None:
    expected = (SCHEMA, str(pilot), annotator,
                sha256(pilot / "capture_run.json"))
    actual = (review.get("schema"), review.get("source_capture"),
              review.get("annotator"), review.get("source_capture_run_sha256"))
    if actual != expected:
        raise ValueError(
            "Existing rating file belongs to another capture or annotator. "
            "Choose a different --output path.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot-dir", required=True, help="completed pilot capture folder")
    parser.add_argument("--output", help="rating JSON; default is <pilot-dir>/light_ratings.json")
    parser.add_argument("--annotator", default="Reviewer")
    args = parser.parse_args()

    pilot = Path(args.pilot_dir).resolve()
    if not completed_pilot(pilot):
        parser.error(f"not a completed enhanced-light pilot: {pilot}")
    output = (Path(args.output).resolve() if args.output else pilot / "light_ratings.json")
    csv_output = output.with_suffix(".csv")
    scenarios = ordered_scenarios(pilot)
    by_id = {row["sid"]: row for row in scenarios}
    review = (read_json(output) if output.is_file()
              else new_review(pilot, scenarios, args.annotator))
    validate_review(review, pilot, args.annotator)

    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.title("Enhanced Runway Light Rating")
    root.geometry("1500x940")
    root.minsize(1000, 700)
    root.configure(bg="#171a1f")

    header = tk.StringVar()
    progress = tk.StringVar()
    note = tk.StringVar()
    state = {
        "index": 0,
        "image": None,
        "tk_image": None,
        "scale": 1.0,
        "offset_x": 0.0,
        "offset_y": 0.0,
        "fit": True,
        "pan": None,
    }
    rating_buttons: dict[int, tk.Button] = {}

    title = tk.Label(root, textvariable=header, bg="#171a1f", fg="white",
                     font=("Segoe UI", 13, "bold"), anchor="w")
    title.pack(fill="x", padx=12, pady=(8, 2))
    help_label = tk.Label(
        root,
        text=("1 = extremely overexposed   |   5 = acceptable   |   10 = perfect   "
              "|   Keys 1-9, 0=10, U=not visible, Left/Right=navigate"),
        bg="#171a1f", fg="#cbd1dc", font=("Segoe UI", 10), anchor="w")
    help_label.pack(fill="x", padx=12, pady=(0, 5))

    canvas = tk.Canvas(root, bg="#090b0e", highlightthickness=0)
    canvas.pack(fill="both", expand=True, padx=10, pady=2)

    progress_label = tk.Label(root, textvariable=progress, bg="#171a1f",
                              fg="#d9dee8", font=("Segoe UI", 10), anchor="w")
    progress_label.pack(fill="x", padx=12, pady=(4, 1))

    rating_bar = tk.Frame(root, bg="#171a1f")
    rating_bar.pack(fill="x", padx=9, pady=4)
    colors = {
        1: "#c62828", 2: "#d84315", 3: "#ef6c00", 4: "#f9a825", 5: "#fdd835",
        6: "#c0ca33", 7: "#7cb342", 8: "#43a047", 9: "#168b4b", 10: "#087f3f",
    }

    notes_bar = tk.Frame(root, bg="#171a1f")
    notes_bar.pack(fill="x", padx=10, pady=(1, 7))
    tk.Label(notes_bar, text="Optional note:", bg="#171a1f", fg="white",
             font=("Segoe UI", 10)).pack(side="left", padx=(2, 6))
    note_entry = tk.Entry(notes_bar, textvariable=note, font=("Segoe UI", 10))
    note_entry.pack(side="left", fill="x", expand=True)

    def current_scenario() -> dict:
        return scenarios[state["index"]]

    def current_record(create: bool = True) -> dict | None:
        sid = current_scenario()["sid"]
        record = review["images"].get(sid)
        if record is None and create:
            scenario = current_scenario()
            image_path = pilot / "images" / f"{sid}.png"
            record = {
                "scenario_id": sid,
                "icao": scenario["icao"],
                "runway": scenario["runway"],
                "ground_distance_m": float(scenario["gd"]),
                "image_file": f"images/{sid}.png",
                "image_sha256": sha256(image_path),
                "decision": "unreviewed",
                "rating": None,
                "note": "",
                "reviewed_utc": None,
            }
            review["images"][sid] = record
        return record

    def processed(record: dict | None) -> bool:
        return bool(record and record.get("decision") in FINAL_DECISIONS)

    def save_note_to_record() -> None:
        record = current_record()
        record["note"] = note.get().strip()

    def write_csv() -> None:
        fields = ["scenario_id", "icao", "runway", "ground_distance_m",
                  "decision", "rating", "note", "reviewed_utc", "image_file",
                  "image_sha256"]
        temporary = csv_output.with_name(csv_output.name + ".tmp")
        with temporary.open("w", newline="", encoding="utf-8-sig") as destination:
            writer = csv.DictWriter(destination, fieldnames=fields)
            writer.writeheader()
            for sid in review["scenario_order"]:
                record = review["images"].get(sid)
                if record:
                    writer.writerow({field: record.get(field, "") for field in fields})
        temporary.replace(csv_output)

    def save() -> None:
        if state["image"] is not None:
            save_note_to_record()
        finished = sum(processed(review["images"].get(row["sid"])) for row in scenarios)
        review["updated_utc"] = utc_now()
        review["status"] = "COMPLETE" if finished == len(scenarios) else "IN_PROGRESS"
        write_json_atomic(output, review)
        write_csv()

    def update_buttons() -> None:
        record = current_record()
        selected = record.get("rating") if record.get("decision") == "rated" else None
        for value, button in rating_buttons.items():
            button.configure(relief="sunken" if value == selected else "raised",
                             bd=4 if value == selected else 2)

    def render() -> None:
        image = state["image"]
        if image is None:
            return
        canvas.delete("all")
        canvas_width = max(1, canvas.winfo_width())
        canvas_height = max(1, canvas.winfo_height())
        if state["fit"]:
            state["scale"] = min(canvas_width / image.width,
                                 canvas_height / image.height) * 0.98
            state["offset_x"] = (canvas_width - image.width * state["scale"]) / 2
            state["offset_y"] = (canvas_height - image.height * state["scale"]) / 2
        display_width = max(1, round(image.width * state["scale"]))
        display_height = max(1, round(image.height * state["scale"]))
        resized = image.resize((display_width, display_height), Image.Resampling.LANCZOS)
        state["tk_image"] = ImageTk.PhotoImage(resized)
        canvas.create_image(state["offset_x"], state["offset_y"],
                            image=state["tk_image"], anchor="nw")

    def update_text() -> None:
        scenario = current_scenario()
        record = current_record()
        score = record.get("rating")
        decision = record.get("decision", "unreviewed")
        score_text = str(score) if score is not None else (
            "not visible" if decision == "unrated_not_visible" else "not rated")
        header.set(
            f"{state['index'] + 1}/{len(scenarios)}   {scenario['sid']}   "
            f"{scenario['icao']} RWY {scenario['runway']}   "
            f"{float(scenario['gd']) / 1000:.1f} km   Current: {score_text}")
        rated = sum(
            review["images"].get(row["sid"], {}).get("decision") == "rated"
            for row in scenarios)
        invisible = sum(
            review["images"].get(row["sid"], {}).get("decision") == "unrated_not_visible"
            for row in scenarios)
        progress.set(
            f"Processed {rated + invisible}/{len(scenarios)}   |   Rated {rated}   |   "
            f"Not visible {invisible}   |   Saved continuously to {output.name}")
        update_buttons()

    def load(index: int) -> None:
        if state["image"] is not None:
            save()
        state["index"] = max(0, min(len(scenarios) - 1, index))
        scenario = current_scenario()
        image_path = (pilot / "images" / f"{scenario['sid']}.png").resolve()
        if not image_path.is_relative_to(pilot):
            raise ValueError("Unsafe image path")
        with Image.open(image_path) as opened:
            state["image"] = opened.convert("RGB")
        record = current_record()
        if record["image_sha256"] != sha256(image_path):
            raise ValueError(f"Image changed since rating began: {scenario['sid']}")
        note.set(record.get("note", ""))
        state["fit"] = True
        root.update_idletasks()
        update_text()
        render()
        canvas.focus_set()

    def advance_after_action() -> None:
        save()
        if state["index"] < len(scenarios) - 1:
            load(state["index"] + 1)
        else:
            update_text()
            progress.set(progress.get() + "   |   Review complete; you may Save and close")

    def rate(value: int) -> None:
        record = current_record()
        record.update(decision="rated", rating=value,
                      note=note.get().strip(), reviewed_utc=utc_now())
        advance_after_action()

    def mark_not_visible() -> None:
        record = current_record()
        record.update(decision="unrated_not_visible", rating=None,
                      note=note.get().strip(), reviewed_utc=utc_now())
        advance_after_action()

    def clear_current() -> None:
        record = current_record()
        record.update(decision="unreviewed", rating=None, reviewed_utc=None)
        save()
        update_text()

    def previous() -> None:
        load(state["index"] - 1)

    def next_image() -> None:
        load(state["index"] + 1)

    def fit_image() -> None:
        state["fit"] = True
        render()

    def zoom(event) -> None:
        image = state["image"]
        if image is None:
            return
        old = state["scale"]
        new = max(0.1, min(8.0, old * (1.2 if event.delta > 0 else 1 / 1.2)))
        state["offset_x"] = event.x - (event.x - state["offset_x"]) * new / old
        state["offset_y"] = event.y - (event.y - state["offset_y"]) * new / old
        state["scale"] = new
        state["fit"] = False
        render()

    def pan_start(event) -> None:
        state["pan"] = (event.x, event.y, state["offset_x"], state["offset_y"])

    def pan_move(event) -> None:
        if state["pan"]:
            x, y, offset_x, offset_y = state["pan"]
            state["offset_x"] = offset_x + event.x - x
            state["offset_y"] = offset_y + event.y - y
            state["fit"] = False
            render()

    def save_note() -> None:
        save()
        canvas.focus_set()
        update_text()

    def close() -> None:
        save()
        root.destroy()

    def key(event) -> None:
        if root.focus_get() == note_entry:
            return
        if event.char in "123456789":
            rate(int(event.char))
        elif event.char == "0":
            rate(10)
        elif event.char.lower() == "u":
            mark_not_visible()
        elif event.keysym == "Left":
            previous()
        elif event.keysym == "Right":
            next_image()
        elif event.char.lower() == "f":
            fit_image()

    for value in range(1, 11):
        button = tk.Button(
            rating_bar, text=str(value), command=lambda score=value: rate(score),
            bg=colors[value], fg="white" if value not in {5, 6} else "#111111",
            activebackground=colors[value], font=("Segoe UI", 11, "bold"),
            width=4, padx=2, pady=4)
        button.pack(side="left", padx=2)
        rating_buttons[value] = button
    tk.Button(rating_bar, text="Unrated / not visible (U)", command=mark_not_visible,
              bg="#596273", fg="white", font=("Segoe UI", 10), padx=9, pady=5).pack(
                  side="left", padx=(12, 2))
    tk.Button(rating_bar, text="Previous", command=previous, padx=8, pady=5).pack(
        side="left", padx=(12, 2))
    tk.Button(rating_bar, text="Next", command=next_image, padx=8, pady=5).pack(
        side="left", padx=2)
    tk.Button(rating_bar, text="Clear", command=clear_current, padx=8, pady=5).pack(
        side="left", padx=2)
    tk.Button(rating_bar, text="Fit image (F)", command=fit_image, padx=8, pady=5).pack(
        side="left", padx=2)
    tk.Button(rating_bar, text="Save and close", command=close, padx=8, pady=5).pack(
        side="right", padx=2)
    tk.Button(notes_bar, text="Save note", command=save_note).pack(side="left", padx=(6, 0))

    canvas.bind("<MouseWheel>", zoom)
    canvas.bind("<Button-2>", pan_start)
    canvas.bind("<B2-Motion>", pan_move)
    canvas.bind("<Button-3>", pan_start)
    canvas.bind("<B3-Motion>", pan_move)
    canvas.bind("<Configure>", lambda _event: render() if state["fit"] else None)
    root.bind("<Key>", key)
    note_entry.bind("<Return>", lambda _event: save_note())
    root.protocol("WM_DELETE_WINDOW", close)
    root.report_callback_exception = lambda _exc, value, _trace: messagebox.showerror(
        "Rating error", f"{value}\n\nSaved ratings are preserved.")

    first_pending = next(
        (index for index, row in enumerate(scenarios)
         if not processed(review["images"].get(row["sid"]))),
        0)
    load(first_pending)
    root.mainloop()


if __name__ == "__main__":
    main()
