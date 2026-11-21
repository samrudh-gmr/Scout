from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_reviewer.ai_review.models import ReviewPolicy, ReviewRequest, ReviewResponse
from video_reviewer.ai_review.providers.gemini import GeminiProvider
from video_reviewer.manifest import (
    REVIEW_APPROVED,
    REVIEW_NEEDS_REVIEW,
    REVIEW_PENDING,
    ManifestRow,
    read_manifest_csv,
    write_manifest_csv,
)

CHOICES = ["RV Front Cap", "Truck Hood"]
BASE = {"client_or_location": "GMR HQ", "confidence": 0.95, "rationale": "r", "flags": []}


def _manifest(tmp_path: Path, part_choices: list[str] | None = None) -> Path:
    frame = tmp_path / "frame_00.jpg"
    frame.write_bytes(b"\xff\xd8\xff\xd9")
    source = tmp_path / "clip.mov"
    source.write_bytes(b"0")
    manifest = tmp_path / "manifest.csv"
    hints = {"part_choices": part_choices} if part_choices is not None else {}
    write_manifest_csv(
        manifest,
        [
            ManifestRow(
                source_path=str(source.resolve()),
                sample_frames=str(frame.resolve()),
                year_month="2024-07",
                sequence="001",
                review_status=REVIEW_PENDING,
                source_hints=json.dumps(hints),
            )
        ],
    )
    return manifest


def _review(monkeypatch, tmp_path: Path, payload: dict, part_choices: list[str] | None = CHOICES):
    from video_reviewer.ai_review import review_rows_with_ai

    manifest = _manifest(tmp_path, part_choices)
    monkeypatch.setattr(GeminiProvider, "_sdk_installed", lambda self: True)
    monkeypatch.setattr(GeminiProvider, "_generate", lambda self, key, model, prompt, frames: dict(payload))
    result = review_rows_with_ai(manifest, [0], provider_id="gemini", api_key="k")[0]
    return result, read_manifest_csv(manifest)[0]


# ── parsing ──────────────────────────────────────────────────────────────────

def test_part_choices_are_split_cleaned_and_deduplicated() -> None:
    from video_reviewer.run_context import parse_part_choices

    assert parse_part_choices(" Truck Hood,  RV Front Cap\ntruck hood ;; ") == ["Truck Hood", "RV Front Cap"]
    assert parse_part_choices("") == []
    assert parse_part_choices(None) == []
    assert parse_part_choices(["Truck Hood", " "]) == ["Truck Hood"]


def test_part_choice_that_cannot_be_a_filename_field_is_rejected() -> None:
    from video_reviewer.run_context import parse_part_choices

    with pytest.raises(ValueError, match="Truck_Hood"):
        parse_part_choices("Truck_Hood")
    with pytest.raises(ValueError):
        parse_part_choices("Hood/Cap")


def test_hints_without_part_choices_read_as_no_choices() -> None:
    from video_reviewer.run_context import part_choices_from_hints

    assert part_choices_from_hints({}) == []
    assert part_choices_from_hints({"part_choices": "not a list"}) == []
    assert part_choices_from_hints({"part_choices": ["Truck Hood", 7, ""]}) == ["Truck Hood"]


# ── prepare ──────────────────────────────────────────────────────────────────

def _fake_prepare(monkeypatch) -> None:
    monkeypatch.setattr("video_reviewer.workflow.require_fftools", lambda: None)
    monkeypatch.setattr("video_reviewer.workflow.probe_media", lambda path: {"capture_time": "", "duration": "1"})
    monkeypatch.setattr("video_reviewer.workflow.run_ffmpeg_proxy", lambda source, proxy, scale: proxy.write_bytes(b"proxy"))
    monkeypatch.setattr("video_reviewer.workflow.extract_sample_frames", lambda *args, **kwargs: [])


def test_prepare_saves_part_choices_on_every_row(monkeypatch, tmp_path: Path) -> None:
    from video_reviewer.workflow import build_prepare_manifest

    source_dir = tmp_path / "videos"
    source_dir.mkdir()
    (source_dir / "one.mov").write_bytes(b"0")
    (source_dir / "two.mov").write_bytes(b"0")
    _fake_prepare(monkeypatch)

    rows = build_prepare_manifest(
        input_dir=source_dir, year_month="2024-07", part_choices=CHOICES,
        start_seq=1, tmp_dir=tmp_path / "tmp", proxy_scale=1280, sample_count=1,
    ).rows

    assert [json.loads(row.source_hints)["part_choices"] for row in rows] == [CHOICES, CHOICES]
    # The choices are context for the model, never a pre-filled answer.
    assert [row.part for row in rows] == ["", ""]


def test_prepare_without_part_choices_is_unchanged(monkeypatch, tmp_path: Path) -> None:
    from video_reviewer.workflow import build_prepare_manifest

    source_dir = tmp_path / "videos"
    source_dir.mkdir()
    (source_dir / "one.mov").write_bytes(b"0")
    _fake_prepare(monkeypatch)

    rows = build_prepare_manifest(
        input_dir=source_dir, year_month="2024-07", start_seq=1, tmp_dir=tmp_path / "tmp", proxy_scale=1280, sample_count=1,
    ).rows

    assert json.loads(rows[0].source_hints)["part_choices"] == []


def test_cli_prepare_takes_part_context() -> None:
    from video_reviewer.cli import build_parser

    args = build_parser().parse_args(
        ["prepare", "--input", "in", "--manifest", "m.csv", "--part-context", "RV Front Cap, Truck Hood"]
    )
    assert args.part_context == "RV Front Cap, Truck Hood"
    assert build_parser().parse_args(["prepare", "--input", "in", "--manifest", "m.csv"]).part_context == ""


def test_gui_prepare_passes_part_choices_and_rejects_bad_ones(monkeypatch, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from video_reviewer.gui import create_app
    from video_reviewer.workflow import PrepareResult

    seen: dict = {}

    def fake_build(**kwargs):
        seen.update(kwargs)
        return PrepareResult(rows=[ManifestRow(source_path=str(tmp_path / "a.mov"), review_status=REVIEW_PENDING)], skipped=[])

    monkeypatch.setattr("video_reviewer.workflow.build_prepare_manifest", fake_build)
    folder = tmp_path / "in"
    folder.mkdir()
    client = TestClient(create_app(tmp_path / "manifest.csv"))

    ok = client.post("/api/run-prepare", json={"input_dir": str(folder), "tmp_dir": str(tmp_path / "c"), "part_choices": "RV Front Cap\nTruck Hood"})
    assert ok.status_code == 200
    assert seen["part_choices"] == ["RV Front Cap", "Truck Hood"]

    seen.clear()
    bad = client.post("/api/run-prepare", json={"input_dir": str(folder), "tmp_dir": str(tmp_path / "c"), "part_choices": "Truck_Hood"})
    assert bad.status_code == 422
    assert "Truck_Hood" in bad.json()["error"]
    assert not seen  # nothing was prepared


# ── prompt ───────────────────────────────────────────────────────────────────

def _prompt(hints: dict) -> str:
    from video_reviewer.ai_review.prompt import prompt_for

    return prompt_for(ReviewRequest(source_name="a.mov", year_month="2024-07", capture_time="", source_hints=hints, frames=[]))


def test_prompt_lists_the_choices_and_allows_none_of_them() -> None:
    prompt = _prompt({"part_choices": CHOICES})
    section = prompt.split("Part choices for this batch")[1]
    assert '"RV Front Cap"' in section and '"Truck Hood"' in section
    assert "empty string" in section


def test_prompt_has_no_part_choice_section_without_choices() -> None:
    assert "Part choices for this batch" not in _prompt({})
    assert "Part choices for this batch" not in _prompt({"part_choices": []})


def test_prompt_tells_operator_clips_not_to_name_the_part() -> None:
    assert "Operators Loading Part" in _prompt({})


# ── decision ─────────────────────────────────────────────────────────────────

def test_part_from_the_choices_is_approved_with_the_operators_spelling(monkeypatch, tmp_path: Path) -> None:
    result, row = _review(
        monkeypatch, tmp_path,
        {**BASE, "scene": "robot_process", "part": "truck  hood", "industry": "Specialty Vehicle", "description": "Sanding"},
    )
    assert result.status == REVIEW_APPROVED
    assert row.part == "Truck Hood"
    assert result.proposed_name == "2024-07_Truck Hood_Sanding_SV_GMR HQ_001.mov"


def test_part_outside_the_choices_is_kept_but_sent_to_review(monkeypatch, tmp_path: Path) -> None:
    result, row = _review(
        monkeypatch, tmp_path,
        {**BASE, "scene": "robot_process", "part": "Boat Hardtop", "industry": "Marine & Boat Building", "description": "Sanding"},
    )
    assert result.status == REVIEW_NEEDS_REVIEW
    assert row.part == "Boat Hardtop"  # never forced onto a listed part
    assert "part_not_in_choices" in row.ai_flags


def test_no_part_on_a_process_clip_goes_to_review_when_choices_were_given(monkeypatch, tmp_path: Path) -> None:
    result, row = _review(
        monkeypatch, tmp_path,
        {**BASE, "scene": "robot_process", "part": "", "industry": "", "description": "Sanding Vehicle Body Panel"},
    )
    assert result.status == REVIEW_NEEDS_REVIEW
    assert row.part == ""
    assert "part_not_identified" in row.ai_flags


def test_operator_clip_stays_partless_and_approved_even_with_choices(monkeypatch, tmp_path: Path) -> None:
    result, row = _review(
        monkeypatch, tmp_path,
        {**BASE, "scene": "operators", "part": "", "industry": "", "description": "Operators Loading Part"},
    )
    assert result.status == REVIEW_APPROVED
    assert result.proposed_name == "2024-07_Operators Loading Part_GMR HQ_001.mov"
    assert row.ai_flags == ""


def test_without_choices_any_part_is_still_accepted(monkeypatch, tmp_path: Path) -> None:
    result, row = _review(
        monkeypatch, tmp_path,
        {**BASE, "scene": "robot_process", "part": "Boat Hardtop", "industry": "Marine & Boat Building", "description": "Sanding"},
        part_choices=None,
    )
    assert result.status == REVIEW_APPROVED
    assert row.ai_flags == ""


@pytest.mark.parametrize("flag", ["part_uncertain", "uncertain_part_industry", "ambiguous_part_type", "industry_uncertain", "part_subtype_unclear"])
def test_model_unsure_about_the_part_goes_to_review(monkeypatch, tmp_path: Path, flag: str) -> None:
    result, _ = _review(
        monkeypatch, tmp_path,
        {**BASE, "scene": "robot_process", "part": "Truck Hood", "industry": "Specialty Vehicle", "description": "Sanding", "flags": [flag]},
    )
    assert result.status == REVIEW_NEEDS_REVIEW


@pytest.mark.parametrize("flag", ["videographer_visible", "bright_glare", "distant_view"])
def test_unrelated_flags_do_not_block_approval(monkeypatch, tmp_path: Path, flag: str) -> None:
    result, _ = _review(
        monkeypatch, tmp_path,
        {**BASE, "scene": "robot_process", "part": "Truck Hood", "industry": "Specialty Vehicle", "description": "Sanding", "flags": [flag]},
    )
    assert result.status == REVIEW_APPROVED


def test_operator_description_that_names_a_part_goes_to_review(monkeypatch, tmp_path: Path) -> None:
    result, row = _review(
        monkeypatch, tmp_path,
        {**BASE, "scene": "operators", "part": "", "industry": "", "description": "Operators Loading Truck Hood"},
    )
    assert result.status == REVIEW_NEEDS_REVIEW
    assert "part_named_on_operator_clip" in row.ai_flags


def test_decide_takes_part_choices_directly() -> None:
    from video_reviewer.ai_review.decide import decide

    response = ReviewResponse(
        description="Sanding", client_or_location="Acme", is_manual=False, confidence=0.9,
        part="rv front cap", industry="Consumer and Recreation", scene="robot_process",
    )
    decision = decide(response, policy=ReviewPolicy(), part_choices=CHOICES)
    assert decision.part == "RV Front Cap"
    assert decision.auto_approve is True


# ── Claude Desktop (MCP) path ────────────────────────────────────────────────

def test_mcp_review_shows_the_part_choices(monkeypatch, tmp_path: Path) -> None:
    from video_reviewer import mcp_server

    manifest = _manifest(tmp_path, CHOICES)
    monkeypatch.setattr(mcp_server, "_get_manifest_path", lambda: manifest)
    text = "\n".join(block.get("text", "") for block in mcp_server.review_video(0))
    assert "RV Front Cap" in text and "Truck Hood" in text
    assert "one of the part choices" in text


def test_mcp_approval_outside_the_choices_waits_for_a_person(monkeypatch, tmp_path: Path) -> None:
    from video_reviewer import mcp_server

    manifest = _manifest(tmp_path, CHOICES)
    monkeypatch.setattr(mcp_server, "_get_manifest_path", lambda: manifest)
    message = mcp_server.approve_video(
        0, description="Sanding", client_or_location="GMR HQ", part="Boat Hardtop", industry="Marine & Boat Building",
    )
    row = read_manifest_csv(manifest)[0]
    assert row.review_status == REVIEW_NEEDS_REVIEW
    assert "part_not_in_choices" in row.ai_flags
    assert "needs review" in message.lower()


def test_mcp_approval_inside_the_choices_is_approved(monkeypatch, tmp_path: Path) -> None:
    from video_reviewer import mcp_server

    manifest = _manifest(tmp_path, CHOICES)
    monkeypatch.setattr(mcp_server, "_get_manifest_path", lambda: manifest)
    mcp_server.approve_video(0, description="Sanding", client_or_location="GMR HQ", part="truck hood", industry="Specialty Vehicle")
    row = read_manifest_csv(manifest)[0]
    assert row.review_status == REVIEW_APPROVED
    assert row.part == "Truck Hood"
