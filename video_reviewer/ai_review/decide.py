"""Turn a model's answer into row fields. Pure: no I/O, no provider, no clock.

Every naming rule the code must hold regardless of what a model says lives
here, so it can be tested without a provider and replayed from a trace.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from video_reviewer.ai_review.models import ReviewPolicy, ReviewResponse

SCENE_ROBOT = "robot_process"
SCENE_MANUAL = "manual_process"
SCENE_OPERATORS = "operators"
SCENES = (SCENE_ROBOT, SCENE_MANUAL, SCENE_OPERATORS)

FLAG_PART_DROPPED = "part_dropped_for_operators"

_SERIOUS_FLAGS = {"client_unknown", "unknown_client", "unsafe", "invalid", FLAG_PART_DROPPED}
_OPERATOR_DESCRIPTION = re.compile(r"^\s*operators?\b", re.IGNORECASE)


@dataclass(frozen=True)
class Decision:
    description: str
    part: str
    industry: str
    client_or_location: str
    confidence: float
    scene: str
    auto_approve: bool
    flags: list[str] = field(default_factory=list)


def normalize_scene(value: object) -> str:
    """One of SCENES, or "" when the model gave nothing usable."""
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return text if text in SCENES else ""


def is_operator_clip(scene: str, description: str) -> bool:
    """A clip about the people, not about a part being worked.

    The description counts on its own so the rule also holds for providers or
    callers that never send a scene.
    """
    return normalize_scene(scene) == SCENE_OPERATORS or bool(_OPERATOR_DESCRIPTION.match(description or ""))


def decide(response: ReviewResponse, *, batch_client_override: str = "", policy: ReviewPolicy) -> Decision:
    description = response.description.strip()
    part = response.part.strip()
    industry = response.industry.strip()
    flags = list(response.flags)
    scene = normalize_scene(response.scene)

    operators = is_operator_clip(scene, description)
    if operators:
        scene = SCENE_OPERATORS
        # Operator footage is never named after a part, whatever the model or
        # the run context says. If the model named one anyway it contradicted
        # itself, so a person confirms the clip.
        if part or industry:
            flags.append(FLAG_PART_DROPPED)
        part = ""
        industry = ""
    elif (response.is_manual or scene == SCENE_MANUAL) and not description.lower().startswith("manual"):
        # The SOP prefixes manual work with "Manual". Applied here rather than
        # trusting each model to remember.
        description = f"Manual {description}"

    if not part:
        industry = ""

    # A client chosen on Prepare is authoritative batch metadata. The model can
    # still identify the activity, but may not replace an operator override.
    client = batch_client_override.strip() or response.client_or_location.strip()
    confidence = max(0.0, min(1.0, float(response.confidence)))
    unknown_client = client.lower() == "unknown"
    auto_approve = (
        confidence >= policy.confidence_threshold
        and not (unknown_client and not policy.allow_unknown_client)
        and not any(flag.lower() in _SERIOUS_FLAGS for flag in flags)
    )
    return Decision(
        description=description,
        part=part,
        industry=industry,
        client_or_location=client,
        confidence=confidence,
        scene=scene,
        auto_approve=auto_approve,
        flags=flags,
    )
