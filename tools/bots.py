"""
Simple rule-based bot teammates for SituSim.

Use them to fill roles nobody is playing (for example in a two-person demo),
or to test the whole system quickly.

    python tools/bots.py --roles ISR EW COMMS
    python tools/bots.py --roles GTL ISR EW COMMS --host 192.168.1.20:8000
    python tools/bots.py --roles EW COMMS --host https://your-app.onrender.com

Bots are deliberately imperfect, so the review has something to show:
they pass on critical reports, acknowledge messages, keep a "read" on each
key question, grade some reports, switch to the relay or a runner when the
radio is down, and make reasonable decisions. The Ground Team Lead bot
trusts Patrol-2 too much and is slow to change its mind (anchoring).
"""

import argparse
import asyncio
import json
import random

import websockets

TITLES = {"GTL": "Ground Team Lead", "ISR": "Air / ISR Monitor", "EW": "Cyber / EW Officer", "COMMS": "Comms Coordinator"}

# Which reports each bot passes on, and to whom.
PASS_ON = {
    "ISR": {"R2": ["GTL"], "R4": ["GTL", "COMMS"], "R7": ["GTL"]},
    "EW": {"R3": ["GTL", "COMMS"], "R6": ["GTL", "COMMS"]},
    "COMMS": {},
    "GTL": {},
}

# How each report moves each key question (what a bot "thinks" a report means).
REPORT_PUSH = {
    "R1": {"Q1": 15}, "R1-B": {"Q1": -12},
    "R2": {"Q1": -10}, "R2-B": {"Q1": 5},
    "R4": {"Q1": -40},
    "R5": {"Q1": 30},
    "R6": {"Q2": -55},
    "R7": {"Q3": 35}, "R7-B": {"Q3": -25},
    "R8": {"Q1": -20},
}
START_READS = {"Q1": 60, "Q2": 80, "Q3": 50}

# Bots grade a few reports. The Ground Team Lead bot trusts the spoofed R5.
GRADES = {
    "GTL": {"R5": ("B", "2"), "R1": ("B", "2")},
    "ISR": {"R4": ("B", "1"), "R1": ("B", "2")},
    "EW": {"R6": ("B", "1")},
    "COMMS": {"R3": ("C", "3")},
}


def pick_decision(role, dp, visible):
    """Return (choice, rationale, cited) using simple rules."""
    dp_id = dp["dp_id"]
    ids = [r for r in visible]

    if dp_id == "DP1":
        if "R2" in ids:
            return "VERIFY", "R2 reports a group moving toward B-2. Verifying with ISR before we move.", ["R2"]
        return "PROCEED", "R1 says Bridge B-2 is clear and nothing contradicts it.", ["R1"] if "R1" in ids else []

    if dp_id == "DP2":
        return "RELAY", "The Ground Team Lead is not acknowledging. Using the relay and a runner for critical updates.", []

    if dp_id == "DP3":
        if role == "GTL" and "R5" in ids and "R6" not in ids:
            return "B2", "R5 from Patrol-2 says B-2 is open and they were just there.", ["R5"]
        if "R6" in ids and "R4" in ids:
            return "F1", "R4 says B-2 is blocked and R6 says Patrol-2's net is spoofed, so R5 is suspect. Ford F-1 meets the intent.", ["R4", "R6"]
        if "R4" in ids:
            return "F1", "R4 says B-2 is blocked. Using Ford F-1 as the intent allows.", ["R4"]
        if "R5" in ids:
            return "B2", "R5 says B-2 is open.", ["R5"]
        return "HOLD", "Not enough information on either crossing.", []

    if dp_id == "DP4":
        if "R7" in ids:
            return "COMMIT", "R7 confirms Ford F-1 is passable.", ["R7"]
        return "DELAY", "No confirmation on the route yet.", []

    # Unknown decision point: pick the first option.
    return dp["options"][0]["id"], "Going with the first option based on what I have.", []


def pick_probe_answers(probe, visible):
    answers = {}
    # The bot "knows" the answer only if the report that proves it reached it.
    evidence_map = {"P1Q1": ["R2"], "P1Q2": ["R4"], "P1Q3": ["R4"], "P2Q1": ["R6"], "P2Q2": ["R7"]}
    right_answers = {
        "P1Q1": "Near C3, moving toward Bridge B-2",
        "P1Q2": "No",
        "P1Q3": "It is blocked or delayed at the bridge",
        "P2Q1": "No, it may be spoofed",
        "P2Q2": "Ford F-1",
    }
    for q in probe["questions"]:
        needed = evidence_map.get(q["q_id"], [])
        has_it = False
        for rid in needed:
            if rid in visible:
                has_it = True
        if has_it and q["q_id"] in right_answers:
            answers[q["q_id"]] = right_answers[q["q_id"]]
        else:
            answers[q["q_id"]] = q["options"][-1]   # usually "I don't know"
    return answers


def target_reads(role, visible_with_time, now):
    """Where this bot's reads 'should' be, given what it has seen (with its own biases)."""
    reads = dict(START_READS)
    saw_r6 = None
    for rid, at in visible_with_time:
        if rid == "R6":
            saw_r6 = at
    for rid, at in visible_with_time:
        push = REPORT_PUSH.get(rid, {})
        for q_id in push:
            amount = push[q_id]
            if role == "GTL":
                # Anchoring: the ground lead believes Patrol-2 strongly and
                # takes a long time to accept evidence against the bridge.
                if rid == "R5":
                    amount = 45
                if q_id == "Q1" and amount < 0 and now - at < 90:
                    amount = amount // 3
            if rid == "R5" and saw_r6 is not None and now - saw_r6 > (60 if role == "GTL" else 10):
                amount = 0   # once the spoofing warning sinks in, R5 no longer counts
            reads[q_id] = reads[q_id] + amount
    for q_id in reads:
        if reads[q_id] < 5:
            reads[q_id] = 5
        if reads[q_id] > 95:
            reads[q_id] = 95
    return reads


async def run_bot(role, host, think_min, think_max):
    name = "Bot " + role
    # A hosted copy on https needs a secure WebSocket (wss://); a laptop on the Wi-Fi uses ws://.
    if host.startswith("https://"):
        url = "wss://" + host[len("https://"):]
    elif host.startswith("http://"):
        url = "ws://" + host[len("http://"):]
    else:
        url = "ws://" + host
    url = url.rstrip("/") + "/ws/trainee/" + role + "/" + name.replace(" ", "%20")
    rng = random.Random(role)
    while True:
        try:
            async with websockets.connect(url) as ws:
                print(name, "connected")
                passed = set()
                acked = set()
                decided = set()
                graded = set()
                shared = set()
                dp_seen_at = {}
                probe_done = set()
                next_read_at = 0
                session = None
                async for text in ws:
                    msg = json.loads(text)
                    if msg.get("type") != "state":
                        continue
                    if msg["session_id"] != session:
                        session = msg["session_id"]
                        passed = set()
                        acked = set()
                        decided = set()
                        graded = set()
                        shared = set()
                        dp_seen_at = {}
                        probe_done = set()
                        next_read_at = 0
                    if msg["status"] == "ENDED":
                        continue
                    now = msg["sim_time"]
                    visible = []
                    visible_with_time = []
                    for item in reversed(msg["feed"]):
                        if item["report_id"] not in visible:
                            visible.append(item["report_id"])
                            visible_with_time.append((item["report_id"], item["delivered_at"]))
                    link_down = msg["link"] == "DOWN"

                    # Freeze probe
                    probe = msg.get("probe")
                    if probe and not probe.get("answered") and probe["probe_id"] not in probe_done:
                        probe_done.add(probe["probe_id"])
                        await ws.send(json.dumps({"type": "probe_answer", "probe_id": probe["probe_id"],
                                                  "answers": pick_probe_answers(probe, visible)}))
                        continue

                    # Reads: set them at the start, then update every 15-25 s
                    if msg["status"] in ["READY", "RUNNING"] and now >= next_read_at:
                        next_read_at = now + rng.randint(15, 25)
                        wanted = target_reads(role, visible_with_time, now)
                        for q_id in wanted:
                            current = msg["reads"].get(q_id)
                            if current is None or abs(current["value"] - wanted[q_id]) >= 5:
                                await ws.send(json.dumps({"type": "belief", "q_id": q_id, "value": wanted[q_id]}))

                    if msg["status"] != "RUNNING":
                        continue

                    # Grades
                    for rid in GRADES.get(role, {}):
                        if rid in visible and rid not in graded:
                            graded.add(rid)
                            grade = GRADES[role][rid]
                            await ws.send(json.dumps({"type": "grade", "report_id": rid, "reliability": grade[0],
                                                      "credibility": grade[1]}))

                    # Acknowledge team messages (by runner if our radio is down)
                    for item in msg["feed"]:
                        if item["ackable"] and item["delivery_id"] not in acked:
                            acked.add(item["delivery_id"])
                            await ws.send(json.dumps({"type": "ack", "delivery_id": item["delivery_id"],
                                                      "channel": "runner" if link_down else "radio"}))

                    # Pass on critical reports. Once the ground lead goes quiet (after DP2),
                    # send critical updates through the relay instead of the radio.
                    relay_mode = now >= 330
                    for item in msg["feed"]:
                        targets = PASS_ON.get(role, {}).get(item["report_id"])
                        if role == "COMMS" and item["kind"] == "forward" and item["from_role"] != "GTL":
                            targets = ["GTL"]
                        if not targets:
                            continue
                        channel = "radio"
                        if link_down:
                            channel = "runner"
                        elif role == "COMMS" or (relay_mode and "GTL" in targets):
                            channel = "relay"
                        # Each report is passed on once, plus one resend of R4 through the relay
                        # once the ground lead has gone quiet.
                        key = (item["report_id"], tuple(targets))
                        if relay_mode and item["report_id"] == "R4" and role == "ISR":
                            key = ("R4-relay",)
                        if key not in passed:
                            passed.add(key)
                            await ws.send(json.dumps({"type": "forward", "delivery_id": item["delivery_id"],
                                                      "to_roles": targets, "note": "Passed on by bot",
                                                      "channel": channel}))

                    # Share reads after key reports
                    for rid in ["R4", "R6"]:
                        if rid in visible and rid not in shared and role in ["ISR", "EW"]:
                            shared.add(rid)
                            await ws.send(json.dumps({"type": "share_read", "to": "GTL",
                                                      "channel": "relay" if relay_mode else "radio"}))

                    # Decisions, after a short "thinking" time
                    for dp in msg["open_dps"]:
                        if dp["submitted"] or dp["dp_id"] in decided:
                            continue
                        if dp["dp_id"] not in dp_seen_at:
                            dp_seen_at[dp["dp_id"]] = now + rng.randint(think_min, think_max)
                        if now >= dp_seen_at[dp["dp_id"]]:
                            decided.add(dp["dp_id"])
                            choice, rationale, cited = pick_decision(role, dp, visible)
                            await ws.send(json.dumps({"type": "decision", "dp_id": dp["dp_id"], "choice": choice,
                                                      "rationale": rationale, "cited": cited, "confidence": "Medium"}))
        except (OSError, websockets.exceptions.WebSocketException) as error:
            print(name, "disconnected:", error, "- retrying in 2 s")
            await asyncio.sleep(2)


async def main():
    parser = argparse.ArgumentParser(description="Run bot teammates for SituSim.")
    parser.add_argument("--roles", nargs="+", default=["ISR", "EW", "COMMS"], help="Roles to fill: GTL ISR EW COMMS")
    parser.add_argument("--host", default="localhost:8000", help="Where SituSim is running, e.g. localhost:8000 or https://your-app.onrender.com")
    parser.add_argument("--think-min", type=int, default=10, help="Fastest decision, in simulation seconds")
    parser.add_argument("--think-max", type=int, default=40, help="Slowest decision, in simulation seconds")
    args = parser.parse_args()
    tasks = []
    for role in args.roles:
        role = role.upper()
        if role in TITLES:
            tasks.append(run_bot(role, args.host, args.think_min, args.think_max))
    print("Running bots for:", ", ".join(r.upper() for r in args.roles))
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bots stopped.")
