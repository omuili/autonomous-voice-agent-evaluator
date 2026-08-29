import argparse
import time
from urllib.parse import quote

from twilio.rest import Client

from config import (
    ALLOWED_TARGET_NUMBER,
    MAX_CALL_SECONDS,
    PUBLIC_BASE_URL,
    TRACEVOX_CAMPAIGN_ID,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_PHONE_NUMBER,
    require_settings,
)
from scenarios import SCENARIOS, get_scenario


TERMINAL_CALL_STATUSES = {
    "completed",
    "busy",
    "failed",
    "no-answer",
    "canceled",
}


def twilio_client() -> Client:
    require_settings()
    return Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


def build_callback_urls(
    scenario_id: str,
    campaign_id: str | None = None,
    base_url: str | None = None,
) -> dict[str, str]:
    base = (base_url if base_url is not None else PUBLIC_BASE_URL).rstrip("/")
    query = f"scenario_id={quote(scenario_id, safe='')}"
    if campaign_id:
        query += f"&campaign_id={quote(campaign_id, safe='')}"

    return {
        "voice": f"{base}/voice?{query}",
        "call_status": f"{base}/call-status?{query}",
        "recording_complete": f"{base}/recording-complete?{query}",
    }


def place_call(
    scenario_id: str,
    target_number: str = ALLOWED_TARGET_NUMBER,
    campaign_id: str | None = None,
) -> str:
    require_settings()

    if target_number != ALLOWED_TARGET_NUMBER:
        raise ValueError(
            f"{ALLOWED_TARGET_NUMBER}."
        )

    get_scenario(scenario_id)

    campaign_id = campaign_id or TRACEVOX_CAMPAIGN_ID or None

    urls = build_callback_urls(scenario_id, campaign_id)
    voice_url = urls["voice"]
    call_status_url = urls["call_status"]
    recording_callback_url = urls["recording_complete"]

    call = twilio_client().calls.create(
        to=target_number,
        from_=TWILIO_PHONE_NUMBER,
        url=voice_url,
        method="POST",
        record=True,
        recording_channels="dual",
        recording_status_callback=recording_callback_url,
        recording_status_callback_method="POST",
        recording_status_callback_event=["completed"],
        status_callback=call_status_url,
        status_callback_method="POST",
        status_callback_event=["initiated", "ringing", "answered", "completed"],
        trim="do-not-trim",
        timeout=30,
        time_limit=MAX_CALL_SECONDS,
    )

    print("Scenario:", scenario_id)
    print("Call SID:", call.sid)
    print("From:", TWILIO_PHONE_NUMBER)
    print("To:", target_number)
    if campaign_id:
        print("TraceVox campaign:", campaign_id)

    return call.sid


def wait_for_call(call_sid: str, poll_seconds: float = 2.0) -> str:
    client = twilio_client()

    while True:
        call = client.calls(call_sid).fetch()
        status = call.status
        print("Call status:", status)

        if status in TERMINAL_CALL_STATUSES:
            return status

        time.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Place one voice-agent evaluation call.")
    parser.add_argument(
        "--scenario",
        required=True,
        choices=sorted(SCENARIOS.keys()),
    )
    parser.add_argument(
        "--wait",
        action="store_true",
        help="Wait until Twilio reports the call is finished.",
    )
    parser.add_argument(
        "--campaign-id",
        default=None,
        help=(
            "TraceVox campaign to associate this call with "
            "(defaults to TRACEVOX_CAMPAIGN_ID from the environment)."
        ),
    )
    args = parser.parse_args()

    call_sid = place_call(args.scenario, campaign_id=args.campaign_id)
    if args.wait:
        final_status = wait_for_call(call_sid)
        print("Final status:", final_status)


if __name__ == "__main__":
    main()
