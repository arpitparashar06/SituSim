"""
Self-test: runs a whole exercise at 20x speed with four bot trainees,
then checks the After-Action Review. Takes about a minute.

    1. Start the server in one terminal:   python main.py
    2. Run this in another terminal:       python tools/selftest.py

Note: this starts a new session on the running server.

To test a hosted copy, point it at the address and give the instructor passcode:

    SITUSIM_HOST=https://your-app.onrender.com INSTRUCTOR_PASSCODE=... python tools/selftest.py
"""

import asyncio
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bots

HOST = os.environ.get("SITUSIM_HOST", "localhost:8000")
BASE = HOST if HOST.startswith("http") else "http://" + HOST
PASSCODE = os.environ.get("INSTRUCTOR_PASSCODE", "")


def post(path, body):
    request = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "X-Passcode": PASSCODE})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        return json.loads(error.read())


def get(path):
    request = urllib.request.Request(BASE + path, headers={"X-Passcode": PASSCODE})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read()


async def drive():
    print("Starting a new session at 20x speed...")
    post("/api/control", {"action": "reset"})
    post("/api/config", {"seed": 42, "difficulty": "NORMAL"})
    post("/api/config", {"speed": 20})

    bot_tasks = []
    for role in ["GTL", "ISR", "EW", "COMMS"]:
        bot_tasks.append(asyncio.create_task(bots.run_bot(role, HOST, 5, 25)))
    await asyncio.sleep(2)

    print(post("/api/control", {"action": "start"}))
    probe_started = False
    while True:
        await asyncio.sleep(0.5)
        state = json.loads(get("/api/state"))
        if state["sim_time"] >= 360 and not probe_started:
            probe_started = True
            print("Freeze probe P1 at", state["clock"], post("/api/probe/start", {"probe_id": "P1"}))
        if state["status"] == "ENDED":
            break
    for task in bot_tasks:
        task.cancel()
    post("/api/config", {"speed": 1})
    return state["session_id"]


def main():
    started = time.time()
    session_id = asyncio.run(drive())
    print("Exercise finished in", round(time.time() - started), "seconds. Session", session_id)

    html = get("/aar/" + session_id).decode()
    data = json.loads(get("/aar/" + session_id + "/json"))
    r = data["aar"]
    print("AAR page:", len(html), "characters")
    print("Events in log:", r["event_count"], " Fingerprint:", r["fingerprint"])
    print("Messages:", r["comms"])
    print("Decisions:")
    for card in r["cards"]:
        print("  ", card["dp_id"], card["title"], "->", card["choice"], "|", card["score"]["percent"], "%")
    print("Findings:")
    for f in r["findings"]:
        print("  -", f["text"])
    problems = []
    if r["decision_count"] == 0:
        problems.append("no decisions recorded")
    if "Traceback" in html:
        problems.append("error in AAR page")
    if len(problems) == 0:
        print("\nSelf-test passed. Open " + BASE + "/aar/" + session_id)
    else:
        print("\nSelf-test FAILED:", ", ".join(problems))
        sys.exit(1)


if __name__ == "__main__":
    main()
