import argparse
import time

from call import place_call, wait_for_call
from config import TRACEVOX_CAMPAIGN_ID
from scenarios import DEFAULT_CAMPAIGN, SCENARIOS

DEFAULT_TRACEVOX_CAMPAIGN = "good-ai-voice-eval-2026"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run a sequential voice-agent test campaign. Use only after you have "
            "manually validated call quality."
        )
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=5.0,
        help="Seconds to wait between completed calls.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run every defined scenario instead of the default 10.",
    )
    parser.add_argument(
        "--campaign-id",
        default=None,
        help=(
            "TraceVox campaign that groups every call in this run "
            f"(default: TRACEVOX_CAMPAIGN_ID or '{DEFAULT_TRACEVOX_CAMPAIGN}')."
        ),
    )
    args = parser.parse_args()

    campaign_id = (
        args.campaign_id or TRACEVOX_CAMPAIGN_ID or DEFAULT_TRACEVOX_CAMPAIGN
    )

    scenario_ids = list(SCENARIOS.keys()) if args.all else DEFAULT_CAMPAIGN
    print(f"Campaign contains {len(scenario_ids)} scenarios.")
    print(f"TraceVox campaign: {campaign_id}")

    for index, scenario_id in enumerate(scenario_ids, start=1):
        print(f"\n[{index}/{len(scenario_ids)}] Starting {scenario_id}")
        call_sid = place_call(scenario_id, campaign_id=campaign_id)
        final_status = wait_for_call(call_sid)
        print(f"Finished {scenario_id}: {final_status}")

        if index < len(scenario_ids):
            time.sleep(args.delay)


if __name__ == "__main__":
    main()
