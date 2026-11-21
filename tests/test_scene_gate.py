from __future__ import annotations

from pathlib import Path

from video_reviewer.ai_review.models import ReviewPolicy, ReviewResponse
from video_reviewer.ai_review.providers.gemini import GeminiProvider
from video_reviewer.manifest import (
    REVIEW_APPROVED,
    REVIEW_NEEDS_REVIEW,
    REVIEW_PENDING,
    ManifestRow,
    read_manifest_csv,
    write_manifest_csv,
)


def _manifest(tmp_path: Path) -> Path:
    frame = tmp_path / "frame_00.jpg"
    frame.write_bytes(b"\xff\xd8\xff\xd9")
    source = tmp_path / "clip.mov"
    source.write_bytes(b"0")
    manifest = tmp_path / "manifest.csv"
    write_manifest_csv(
        manifest,
        [
            ManifestRow(
                source_path=str(source.resolve()),
                sample_frames=str(frame.resolve()),
                year_month="2024-07",
                sequence="001",
                review_status=REVIEW_PENDING,
            )
        ],
    )
    return manifest


def _review(monkeypatch, tmp_path: Path, payload: dict):
    from video_reviewer.ai_review import review_rows_with_ai

    manifest = _manifest(tmp_path)
    monkeypatch.setattr(GeminiProvider, "_sdk_installed", lambda self: True)
    monkeypatch.setattr(GeminiProvider, "_generate", lambda self, key, model, prompt, frames: dict(payload))
    result = review_rows_with_ai(manifest, [0], provider_id="gemini", api_key="k")[0]
    return result, read_manifest_csv(manifest)[0]


BASE = {"client_or_location": "GMR HQ", "confidence": 0.95, "rationale": "r", "flags": []}


def test_operator_clip_never_gets_a_part_even_when_the_model_returns_one(monkeypatch, tmp_path: Path) -> None:
    result, row = _review(
        monkeypatch,
        tmp_path,
        {**BASE, "scene": "operators", "part": "Aluminum Wheel", "industry": "Specialty Vehicle",
         "description": "Operators Controlling Robot"},
    )
    assert row.part == "" and row.industry == ""
    assert result.proposed_name == "2024-07_Operators Controlling Robot_GMR HQ_001.mov"
    # The model contradicted itself, so a person confirms rather than auto-approving.
    assert result.status == REVIEW_NEEDS_REVIEW
    assert "part_dropped_for_operators" in row.ai_flags


def test_clean_operator_clip_auto_approves_without_a_manual_prefix(monkeypatch, tmp_path: Path) -> None:
    result, row = _review(
        monkeypatch,
        tmp_path,
        {**BASE, "scene": "operators", "part": "", "industry": "", "is_manual": True,
         "description": "Operators Inspecting Robot"},
    )
    assert result.status == REVIEW_APPROVED
    assert result.proposed_name == "2024-07_Operators Inspecting Robot_GMR HQ_001.mov"
    assert row.ai_flags == ""


def test_operator_description_is_enough_when_the_model_omits_scene(monkeypatch, tmp_path: Path) -> None:
    result, row = _review(
        monkeypatch,
        tmp_path,
        {**BASE, "part": "Boat Hull", "industry": "Marine & Boat Building", "description": "Operators Controlling Robot"},
    )
    assert row.part == "" and row.industry == ""
    assert result.proposed_name == "2024-07_Operators Controlling Robot_GMR HQ_001.mov"


def test_robot_clip_keeps_its_part(monkeypatch, tmp_path: Path) -> None:
    result, row = _review(
        monkeypatch,
        tmp_path,
        {**BASE, "scene": "robot_process", "part": "Aluminum Wheel", "industry": "Specialty Vehicle", "description": "Sanding"},
    )
    assert result.status == REVIEW_APPROVED
    assert result.proposed_name == "2024-07_Aluminum Wheel_Sanding_SV_GMR HQ_001.mov"


def test_manual_scene_adds_the_manual_prefix_and_keeps_the_part(monkeypatch, tmp_path: Path) -> None:
    result, row = _review(
        monkeypatch,
        tmp_path,
        {**BASE, "scene": "manual_process", "part": "Aluminum Wheel", "industry": "Specialty Vehicle", "description": "Sanding"},
    )
    assert result.proposed_name == "2024-07_Aluminum Wheel_Manual Sanding_SV_GMR HQ_001.mov"


def test_unrecognised_scene_value_is_ignored(monkeypatch, tmp_path: Path) -> None:
    result, row = _review(
        monkeypatch,
        tmp_path,
        {**BASE, "scene": "people maybe", "part": "Aluminum Wheel", "industry": "Specialty Vehicle", "description": "Sanding"},
    )
    assert row.part == "Aluminum Wheel"


def test_decide_is_a_pure_function() -> None:
    from video_reviewer.ai_review.decide import decide

    response = ReviewResponse(
        description="Operators Controlling Robot", client_or_location="Acme", is_manual=False, confidence=0.9,
        part="Bracket", industry="General Manufacturing", scene="operators",
    )
    decision = decide(response, batch_client_override="Life Line", policy=ReviewPolicy())
    assert (decision.part, decision.industry) == ("", "")
    assert decision.client_or_location == "Life Line"
    assert decision.auto_approve is False
    assert decision.flags == ["part_dropped_for_operators"]
    # The input is untouched.
    assert response.part == "Bracket"


def test_prompt_asks_for_scene_and_states_the_operator_rule() -> None:
    from video_reviewer.ai_review.models import ReviewRequest
    from video_reviewer.ai_review.prompt import prompt_for

    prompt = prompt_for(ReviewRequest(source_name="a.mov", year_month="2024-07", capture_time="", source_hints={}, frames=[]))
    assert '"operators"' in prompt and "robot_process" in prompt and "manual_process" in prompt
    assert "scene" in prompt.split("Respond with ONLY")[1]


def test_mcp_approval_drops_the_part_for_operator_clips(monkeypatch, tmp_path: Path) -> None:
    from video_reviewer import mcp_server

    manifest = _manifest(tmp_path)
    monkeypatch.setattr(mcp_server, "_get_manifest_path", lambda: manifest)
    message = mcp_server.approve_video(
        0, description="Operators Controlling Robot", client_or_location="GMR HQ",
        part="Aluminum Wheel", industry="Specialty Vehicle",
    )
    row = read_manifest_csv(manifest)[0]
    assert row.part == "" and row.industry == ""
    assert "2024-07_Operators Controlling Robot_GMR HQ_001.mov" in message
