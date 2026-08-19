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

`YYYY-MM_[Part]_[Description]_[IndustryIfPartPresent]_[ClientOrLocation]_SequenceNumber.extension`

Examples:
- `2026-08_Aluminum Wheel_Sanding_Specialty-Vehicle_ClientName_001.mov`
- `2026-08_Sanding Surface Test_ClientName_002.mov`

- Underscores separate fields and appear nowhere else.
- Part and Industry are optional fields and are omitted when empty.
- Spaces are allowed inside Part, Description, and Client/Location.
- Never use: `/ \\ : * ? " < > |`
- No leading or trailing spaces, no double underscores.

## Description

When Part is populated, Description is only the concise process/action, such as
`Sanding` or `Surface Finishing`. When Part is empty, keep the established
**Action + Object** Description behavior. Be specific — these names are
searched.

- When a recognizable part is visible, put its specific name in the separate
  Part field, e.g. `Aluminum Wheel`, `Aircraft Bracket`, or `Boat Propeller`.
- Do not force a generic description such as `Surface Finishing Test` when the
  footage supports a useful object description and Part is empty.
- **Assume the footage is of robots.** Do not write "Robot", "Robot Arm", or
  "Robotic" — it is the default and it makes every name look alike.
- Include an object or surface identifier whenever you can see one, even a
  general one ("Metal Panel" beats nothing).
- Reuse the standard keywords below rather than inventing synonyms.

Good: `Sanding Ambulance Body` · `Grinding Metal Panel` · `Buff and Polish Boat
Hull` · `Paint Spray Automobile Part` · `Inspection Composite Panel` ·
`Scanning Weld Seam` · `Assembly Metal Frame`

## Industry

Only include an industry when a recognizable part is visible. Use exactly one
of these categories, stored with spaces and rendered with hyphens in filenames:

- Specialty Vehicle → `Specialty-Vehicle`
- Marine & Boat Building → `Marine-and-Boat-Building`
- General Manufacturing → `General-Manufacturing`
- Consumer and Recreation → `Consumer-and-Recreation`
- Architecture → `Architecture`
- Aerospace & Defense → `Aerospace-and-Defense`

If there is no identifiable part, leave Industry empty and omit that filename
segment. Do not use generic terms such as `Part`, `Component`, or `Metal Piece`
when the part can be identified with reasonable confidence.

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
2024-03_Composite Aircraft Panel_Sanding_Aerospace-and-Defense_GMR HQ_001.mov
2024-04_Steel Column_Grinding_Architecture_FABTECH Event_001.mov
2024-06_Bicycle Frame_Buff and Polish_Consumer-and-Recreation_GMR HQ_001.mov
2023-09_Welded Metal Frame_Grinding_General-Manufacturing_Performance Composites_001.mov
2024-07_Fiberglass Boat Hull_Buff and Polish_Marine-and-Boat-Building_GMR HQ_001.mov
2022-12_Ambulance Body_Sanding_Specialty-Vehicle_Life Line_001.mov
```
"""


CURRENT_CONVENTION_OVERLAY = """## Current filename convention — Scout

The current convention supersedes any older schema above:

`YYYY-MM_[Part]_[Description]_[IndustryIfPartPresent]_[ClientOrLocation]_SequenceNumber.extension`

Part and Industry are optional. If a recognizable part is present, put its
specific name in Part, use only the process/action in Description, and choose
exactly one approved Industry category. If no part is present, leave both
optional fields empty and keep the established Action + Object Description.
Industry categories: Specialty Vehicle; Marine & Boat Building; General
Manufacturing; Consumer and Recreation; Architecture; Aerospace & Defense.
Render spaces and `&` as hyphens/`and` in filenames, for example
`Specialty-Vehicle` and `Aerospace-and-Defense`.
"""


def _is_untouched_legacy_guide(text: str) -> bool:
    """Recognize only the original shipped guide, not an operator's custom guide."""
    return all(marker in text for marker in (
        "`YYYY-MM_Description_ClientOrLocation_SequenceNumber.extension`",
        "Example: `2022-12_Sanding Ambulance Body_Life Line_001.mov`",
        "2024-03_Sanding Composite Aircraft Panel_GMR HQ_001.mov",
    ))


def _is_previous_industry_guide(text: str) -> bool:
    """Recognize Scout's first industry-guide version for a clean correction."""
    return all(marker in text for marker in (
        "`YYYY-MM_Description_IndustryIfPartPresent_ClientOrLocation_SequenceNumber.extension`",
        "`2026-08_Surface Finishing Aluminum Wheel_Specialty-Vehicle_ClientName_001.mov`",
        "Write **Action + Object** in Title Case",
    ))


def _is_previous_part_action_guide(text: str) -> bool:
    """Recognize the intermediate Part + Action guide for a clean correction."""
    return all(marker in text for marker in (
        "`YYYY-MM_Description_IndustryIfPartPresent_ClientOrLocation_SequenceNumber.extension`",
        "`2026-08_Aluminum Wheel Surface Finishing_Specialty-Vehicle_ClientName_001.mov`",
        "write **Part + Action** in Title Case",
    ))


def ensure_guide() -> Path:
    """Create the guide on first use. An existing file is never overwritten."""
    if not GUIDE_PATH.exists():
        GUIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
        GUIDE_PATH.write_text(DEFAULT_GUIDE, encoding="utf-8")
    else:
        existing = GUIDE_PATH.read_text(encoding="utf-8")
        if _is_untouched_legacy_guide(existing) or _is_previous_industry_guide(existing) or _is_previous_part_action_guide(existing):
            GUIDE_PATH.write_text(DEFAULT_GUIDE, encoding="utf-8")
    return GUIDE_PATH


def load_guide() -> str:
    """The operator's guide text, or the shipped default if it cannot be read."""
    try:
        text = ensure_guide().read_text(encoding="utf-8").strip()
        if "IndustryIfPartPresent" not in text:
            text = f"{text}\n\n{CURRENT_CONVENTION_OVERLAY}"
        return text
    except OSError:
        return DEFAULT_GUIDE.strip()


def reset_guide() -> Path:
    """Overwrite the operator's guide with the shipped default."""
    GUIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
    GUIDE_PATH.write_text(DEFAULT_GUIDE, encoding="utf-8")
    return GUIDE_PATH
