import argparse
import json
from pathlib import Path

from processing import (
    evaluate_call,
    labeled_transcript_text,
    load_authoritative_simulator_utterances,
    raw_transcript_text,
    write_call_report,
)
from storage import call_dir, save_json


def infer_recording_sid(directory: Path) -> str:
    mp3s = sorted(directory.glob("RE*.mp3"))
    if mp3s:
        return mp3s[0].stem
    return "existing-recording"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Re-run evaluation for an existing call without placing a new call."
    )
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--call-sid", required=True)
    args = parser.parse_args()

    directory = call_dir(args.scenario, args.call_sid)
    transcript_json_path = directory / "transcript.json"
    if not transcript_json_path.exists():
        raise FileNotFoundError(
            f"Missing {transcript_json_path}. Use a call that has already been processed."
        )

    transcript = json.loads(transcript_json_path.read_text(encoding="utf-8"))
    raw_text = raw_transcript_text(transcript)
    authoritative = load_authoritative_simulator_utterances(directory)

    evaluation = evaluate_call(args.scenario, raw_text, authoritative)
    save_json(directory / "evaluation.json", evaluation.model_dump())

    (directory / "transcript_raw.txt").write_text(raw_text, encoding="utf-8")
    (directory / "transcript.txt").write_text(
        labeled_transcript_text(transcript, evaluation),
        encoding="utf-8",
    )

    write_call_report(
        args.scenario,
        args.call_sid,
        infer_recording_sid(directory),
        evaluation,
        directory,
    )

    print("Re-evaluation complete")
    print("Failure attribution:", evaluation.failure_attribution)
    print("Target-agent bugs:", len(evaluation.bugs))
    print("Simulator issues:", len(evaluation.simulator_issues))
    print("Infrastructure issues:", len(evaluation.infrastructure_issues))
    print("Report:", directory / "report.md")


if __name__ == "__main__":
    main()
