"""The editable naming guide that steers every AI provider.

The guide lives at ~/.video-renamer/naming_guide.md as plain markdown. Edit that
file when the model keeps getting names wrong — new vocabulary, a client whose
name it mangles, a rule it ignores — and the next review picks it up. No code
change and no restart.

The default below is a transcription of the GMR File Renaming SOP.
"""
from __future__ import annotations

from pathlib import Path

GUIDE_PATH = Path.home() / ".video-renamer" / "naming_guide.md"

DEFAULT_GUIDE = """# Naming guide

Edit this file to correct the model. It is sent with every review request, and
anything you write here outranks the model's own instincts. Keep it short and
concrete — rules and vocabulary, not prose.

## Schema

`YYYY-MM_Description_ClientOrLocation_SequenceNumber.extension`

Example: `2022-12_Sanding Ambulance Body_Life Line_001.mov`

- Underscores separate fields and appear nowhere else.
- Spaces are allowed inside Description and Client/Location.
- Never use: `/ \\ : * ? " < > |`
- No leading or trailing spaces, no double underscores.

## Description

Write **Action + Object** in Title Case. Be specific — these names are searched.

- Describe what is visibly happening to a visible object or surface.
- **Assume the footage is of robots.** Do not write "Robot", "Robot Arm", or
  "Robotic" — it is the default and it makes every name look alike.
- Include an object or surface identifier whenever you can see one, even a
  general one ("Metal Panel" beats nothing).
- Reuse the standard keywords below rather than inventing synonyms.

Good: `Sanding Ambulance Body` · `Grinding Metal Panel` · `Buff and Polish Boat
Hull` · `Paint Spray Automobile Part` · `Inspection Composite Panel` ·
`Scanning Weld Seam` · `Assembly Metal Frame`

### Manual work

If a **person** is performing the task instead of the robot, prefix the
description with `Manual`: `Manual Sanding Aluminum Panel`.

Operators standing near or supervising a working robot are **not** manual work —
use `Operators Controlling Robot` or `Operators Inspecting Robot` instead.

## Standard keywords

**Process:** Sanding · Sand Paper · Grinding · Buff and Polish · Paint Spray ·
Masking · Seam Sealing · Assembly · Inspection · Scanning

**People:** Operators Controlling Robot · Operators Inspecting Robot

**Manual:** Manual Sanding · Manual Grinding · Manual Buff and Polish ·
Manual Paint Spray · Manual Seam Sealing · Manual Inspection

## Client / location / event

Use the same spelling every time so footage groups together in search.

Known values: Performance Composites · FABTECH Event · Automate Showcase ·
GMR HQ · Life Line

Prefer a name found in the source filename. Use `Unknown` only when the frames
and the filename genuinely give you nothing.

## Objects and surfaces by vertical

- **Aerospace & defense:** Aircraft Panel · Composite Aircraft Panel ·
  Aerospace Component · Aircraft Interior Panel
- **Architecture:** Metal Railing · Structural Beam · Architectural Metal Panel ·
  Steel Column
- **Consumer & recreation:** Bicycle Frame · Motorcycle Tank ·
  Recreational Vehicle Panel · Sporting Equipment Frame
- **General manufacturing:** Metal Panel · Aluminum Panel · Welded Metal Frame ·
  Fabricated Part
- **Marine & boat building:** Boat Hull · Fiberglass Hull · Composite Boat Panel ·
  Deck Surface
- **Specialty vehicle:** Ambulance Body · Fire Truck Panel ·
  Utility Vehicle Panel · Emergency Vehicle Door

## Reference filenames

```
2024-03_Sanding Composite Aircraft Panel_GMR HQ_001.mov
2024-04_Grinding Steel Column_FABTECH Event_001.mov
2024-06_Buff and Polish Bicycle Frame_GMR HQ_001.mov
2023-09_Grinding Welded Metal Frame_Performance Composites_001.mov
2024-07_Sanding Fiberglass Boat Hull_GMR HQ_001.mov
2022-12_Sanding Ambulance Body_Life Line_001.mov
```
"""


def ensure_guide() -> Path:
    """Create the guide on first use. An existing file is never overwritten."""
    if not GUIDE_PATH.exists():
        GUIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
        GUIDE_PATH.write_text(DEFAULT_GUIDE, encoding="utf-8")
    return GUIDE_PATH


def load_guide() -> str:
    """The operator's guide text, or the shipped default if it cannot be read."""
    try:
        return ensure_guide().read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULT_GUIDE.strip()


def reset_guide() -> Path:
    """Overwrite the operator's guide with the shipped default."""
    GUIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
    GUIDE_PATH.write_text(DEFAULT_GUIDE, encoding="utf-8")
    return GUIDE_PATH
