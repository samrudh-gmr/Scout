"""The one place the classification prompt is assembled, for every provider."""
from __future__ import annotations

import json

from video_reviewer.ai_review.models import ReviewRequest
from video_reviewer.run_context import part_choices_from_hints

PROMPT_TEMPLATE = """You are classifying a single manufacturing/industrial video for a strict file-naming SOP.
You are given representative video frames and metadata.

Metadata / source hints:
{context}

Determine, from the frames and hints:
- scene: what the clip is about. Exactly one of:
  "robot_process" — a robot or automated cell working a part;
  "manual_process" — a person doing the process by hand;
  "operators" — the clip is about people (controlling, supervising, inspecting or standing by a robot), not about a part being worked.
  When scene is "operators", part and industry MUST be empty strings even if a part is visible, and description is e.g. "Operators Controlling Robot" or "Operators Inspecting Robot".
- part: the specific recognizable industrial part, such as "Aluminum Wheel", "Aircraft Bracket", or "Boat Propeller". Use an empty string if no distinct part can be identified.
- description: when part is non-empty, provide only the concise process/action in Title Case, e.g. "Sanding" or "Surface Finishing". When part is empty, keep the established "Action + Object" description behavior, e.g. "Sanding Automotive Body Panel". Do not force a generic description such as "Surface Finishing Test" when the footage supports a useful object description.
- industry: if and only if part is non-empty, choose exactly one of: "Specialty Vehicle", "Marine & Boat Building", "General Manufacturing", "Consumer and Recreation", "Architecture", "Aerospace & Defense". If part is empty, use an empty string.
- client_or_location: the client company or site name. Prefer filename/location hints. Use "Unknown" only if genuinely impossible.
- is_manual: true if a human operator performing the process is visible.
- confidence: confidence from 0.0 to 1.0 in description + client/location.
- rationale: one short sentence explaining the classification.
- flags: short uncertainty flags, e.g. ["client_unknown", "blurry_frames"].

Hard constraints: part and industry (when non-empty), description, and client_or_location MUST NOT contain underscores or any of these characters: / \\ : * ? " < > |. Description and client_or_location may not be empty. Industry must be empty or one of the six exact categories above.

Respond with ONLY a JSON object with exactly these keys: scene, part, description, industry, client_or_location, is_manual, confidence, rationale, flags."""


def prompt_for(request: ReviewRequest) -> str:
    context = {
        "source_name": request.source_name,
        "year_month": request.year_month,
        "capture_time": request.capture_time,
        "source_hints": request.source_hints,
        "frames": [
            {
                "index": idx,
                "timestamp_seconds": frame.timestamp_seconds,
                "width": frame.width,
                "height": frame.height,
                "downscaled": frame.downscaled,
            }
            for idx, frame in enumerate(request.frames)
        ],
    }
    prompt = PROMPT_TEMPLATE.format(context=json.dumps(context, indent=2, sort_keys=True))

    # Appended, never interpolated: the guide and corrections are operator-owned
    # text and a stray brace would blow up .format().
    from video_reviewer.config import build_corrections_context
    from video_reviewer.naming_guide import load_guide

    sections = [prompt]
    choices = part_choices_from_hints(request.source_hints)
    if choices:
        listed = ", ".join(json.dumps(choice) for choice in choices)
        sections.append(
            "# Part choices for this batch (from the operator)\n"
            f"The operator says the parts in this batch are: {listed}.\n"
            "For a robot_process or manual_process clip, set part to exactly one of these, copied character for character, "
            "choosing the one the frames best support. If the part in the frames is clearly none of them, or you cannot "
            "tell which one it is, set part to an empty string and add the flag \"part_not_identified\" — do not guess and "
            "do not invent another part name. This list does not change the scene rule: operators clips still have no part."
        )
    sections += ["# Naming guide (authoritative — follow it over your own instincts)", load_guide()]
    corrections = build_corrections_context()
    if corrections:
        sections.append(corrections)
    return "\n\n".join(sections)
