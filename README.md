# SituSim

A web-based trainer for small-team decision-making when communications are degraded.
Built for **Smart India Hackathon 2026, Problem Statement 26248** (Ministry of Defence, Defence Services Staff College):
*Immersive Multi-Domain Decision-Making Trainer for Degraded Communication Environments.*

Every trainee gets a **different, deliberately degraded picture**: reports arrive late, go missing or contradict
each other, and links drop mid-exercise. Every decision is saved together with **exactly what that trainee could
see at that moment**, and the After-Action Review judges the decision against that, not against hidden truth.

> Everything in the scenario is fictional (Kestrel Valley, Blue team, "unidentified group").
> EW and cyber appear only as abstract effects on information: delay, loss, spoofing. No real units, places, equipment or techniques.

---

## How it covers the problem statement

| Expected outcome | Where it is in SituSim |
|---|---|
| Scenario engine that injects delay, dropout and conflicting reports mid-exercise | `main.py`: per-trainee delays and losses, timed link outages that recover by themselves, engine-generated contradictions, sensor outages, a spoofed source. Scripted injects plus live instructor injects, five fog levels, and a **Fog Director** that thickens or clears the fog depending on how the team copes. |
| Multiplayer team coordination under degraded information | 4 roles with different feeds. Trainees keep a live **read** on 3 key questions and can share it, **grade** sources (NATO A–F / 1–6), and reach each other by **radio, relay, runner or flare** (PACE). Talking has a cost: enemy **direction finding** locates a role that transmits too much. |
| Instructor dashboard to configure and monitor in real time | `/instructor`: a live network you can click to jam, a **live picture** of every trainee's read against what their reports support and the truth, a "who has what" matrix, contradictions, decisions with rubric scores, injects, freeze probes, settings. |
| Exportable AAR with individual and team decision timelines and rationale | `/aar/<session>`: the picture over time (information gap vs reasoning gap), calibration and Brier scores, a **"see what they saw"** player, per-role lanes, decision cards with rationale and what they could see, detectability, channel switching, source grading, sustain/improve. Download as **one offline HTML file**, print to PDF, or export JSON. |

---

## Quick start

You need **Python 3.10 or newer**.

**Mac / Linux**
```bash
cd SituSim
./run.sh
```

**Windows**
```
cd SituSim
run.bat
```

Or by hand:
```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Then open **http://localhost:8000**. The terminal also prints the address other devices on the same Wi-Fi
or hotspot should use (for example `http://192.168.1.20:8000`).

- **Instructor:** http://localhost:8000/instructor
- **Trainees:** http://localhost:8000, then enter a name and pick a role
- **Latest review:** http://localhost:8000/aar

---

## Running a demo (about 6 minutes)

1. Start the server. Open the instructor console on the main laptop.
2. Join as **Ground Team Lead** on a second laptop or browser window, and as **Air / ISR Monitor** on a phone.
3. Not enough people? Fill the other seats with bots in a second terminal:
   ```bash
   python tools/bots.py --roles EW COMMS
   ```
4. In the instructor console set speed to **2×** and press **Start**.
5. On the home page, show the small playable network first: click a trainee to cut their radio and drag the fog.
6. Point at the same report arriving on two screens at different times. Old reports fog over in the feed and on the map.
7. Ask the trainees to move their **read** sliders as reports arrive. The instructor's **Live picture** shows each read
   against what their reports support (the shadow analyst) and the truth.
8. **Inject live:** click the Ground Team Lead on the network and set their radio to **Down** for 2 minutes. Messages
   queue at the break. ISR switches to **relay** or **runner** to get R4 through.
9. Turn on **Enemy direction finding** and watch the detectability rings fill as people talk.
10. Optional: **Freeze probe** (Inject → Freeze probe). The clock stops and every trainee screen is hidden while they answer.
11. Press **End**, then **Open review**. Hover the small charts (one crosshair moves across all of them), click one to
    replay that trainee's screen at that moment, then show a decision card and the findings. **Download review** gives
    one HTML file that opens offline.
12. Show reproducibility: two runs with the same seed and the same actions give the same **delivery fingerprint**.

Demo-day tips: use a phone hotspot you control, run one server only, delete `situsim.db` for a clean history,
and keep a recorded backup video. Everything, fonts included, is served locally, so no internet is needed.

---

## The scenario: Exercise GREY LANTERN

Blue team must get a relief convoy across the Kestrel river by T+15:00. Bridge B-2 is the fast route, Ford F-1 the slow one.

Built-in learning traps:
- **DP1**: the report about a group heading for the bridge (R2) may arrive late or not at all on the degraded net.
- **R4 vs R5**: the true report (bridge blocked) goes only to ISR; a spoofed one (bridge open) goes only to the Ground Team Lead and arrives fast. Nobody sees both unless the team talks.
- **DP2**: the Ground Team Lead's link goes down; critical updates need the Comms alternate channel.
- **R6**: the cyber clue that R5 was spoofed reaches only the Cyber/EW Officer. Did they push it before the route decision?

The scenario is one JSON file: `scenarios/grey_lantern.json`. Copy it to write your own. Each inject has an `at` time
(simulation seconds) and an `action`: `REPORT`, `LINK`, `SENSOR` or `DECISION_POINT`. Decision points carry an
answer key (`rules`) that says which options are sound **given which reports were visible**.

---

## How the review scores a decision

Each decision is scored 0 to 2 on five dimensions. A dimension that doesn't apply is N/A.

| Dimension | 2 means |
|---|---|
| Fit with available information | The choice matches what that trainee could actually see |
| Handling of conflicting reports | When two reports disagreed, they verified or said which one they trusted and why |
| Awareness of old information | They didn't rely on stale reports without noticing |
| Timeliness | Decided inside the decision window |
| Rationale | The reasoning points to reports or to the commander's intent |

A choice that turned out right **by luck** while ignoring visible information scores lower than a sound choice
that turned out wrong. Freeze probes (SAGAT-style) separate an **information gap** (the answer never reached
them) from an **attention gap** (it was in their feed and they missed it).

The review is rebuilt **only from the append-only event log** in `situsim.db`, so it is auditable.

### Information gap or reasoning gap

A **shadow analyst** reads exactly the reports each trainee had and keeps its own estimate on every key question:
the prior, plus each report's evidence, weighted by how reliable its source usually is and by how fresh the report
is, and discounted when another report says the source is compromised (`shadow.py`, a transparent rule, no AI).
Every moment of every read then falls into one of four categories:

| | The reports pointed the right way | The reports pointed the wrong way |
|---|---|---|
| **The read was right** | Sound read | Ahead of the evidence (luck or instinct) |
| **The read was wrong** | **Reasoning gap**: the person, not the comms | **Information gap**: fix the comms, not the person |

Reads are also scored for **calibration** (were they as sure as they should have been?) with a Brier score per
trainee, next to the shadow analyst's score with the same reports.

---

## Optional: AI-written summary

The review always includes a summary built from the log. You can also have an AI draft a narrative paragraph.
Every sentence must cite real event IDs and use only numbers that are in the log, otherwise it is rejected and the
log-based summary stays. It is off unless you set a key before starting the server:

```bash
# Claude
export ANTHROPIC_API_KEY=your-key
# optional: export LLM_MODEL=claude-haiku-5-5

# or any OpenAI-compatible service (Gemini, Groq, OpenAI, a local Ollama server)
export LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
export LLM_API_KEY=your-key
export LLM_MODEL=the-model-name-your-provider-lists
```
On Windows use `set NAME=value` instead of `export`.

---

## Putting it online (Render, free)

SituSim runs as **one always-on Python process**: the exercise clock, every player and the instructor console
share it. That is why it doesn't fit Vercel-style serverless hosting. Render's free web service runs it as is.

Two settings switch on the hosted mode. On a laptop, leave both empty and nothing changes.

| Setting | What it does |
|---|---|
| `INSTRUCTOR_PASSCODE` | Locks the instructor console, its API, the live state (which includes the ground truth) and the review of a running exercise. Trainees and finished reviews stay open. |
| `DATABASE_URL` | A Postgres connection string. Every event is also copied there in the background and loaded back on start-up, so reviews survive restarts on hosts that wipe local files. |

Steps:

1. Put this folder in a GitHub repository.
2. Create a free Postgres database at [neon.com](https://neon.com) (no card needed) and copy its connection string.
3. On [render.com](https://render.com): **New → Blueprint**, pick the repository. `render.yaml` sets up the service
   (free plan, Singapore). Paste the connection string into `DATABASE_URL` and choose an `INSTRUCTOR_PASSCODE`.
4. Open the `.onrender.com` address. Trainees join from the home page; the instructor console asks for the passcode.

Bots can fill seats on the hosted copy too: `python tools/bots.py --roles EW COMMS --host https://your-app.onrender.com`.
The free service sleeps after 15 minutes with nobody connected and takes about a minute to wake, so open it a
minute before a demo. During an exercise the open connections keep it awake.

---

## Testing

With the server running, in another terminal:
```bash
python tools/selftest.py
```
It runs a whole exercise at 20× speed with four bots (about a minute) and checks the review.
To test a hosted copy: `SITUSIM_HOST=https://your-app.onrender.com INSTRUCTOR_PASSCODE=... python tools/selftest.py`.

---

## Project layout

```
main.py                  server, simulation clock, degradation engine, WebSockets, instructor API
shadow.py                the shadow analyst: what a careful reader with the same reports would believe
aar.py                   After-Action Review: rebuilds everything from the event log, rubric, gaps, calibration
scenarios/grey_lantern.json   the scenario (reports, key questions, link outages, decision points, probes)
templates/aar.html       the review page
static/                  home, trainee, instructor and review pages (plain HTML, CSS, JavaScript) and fonts
static/netgraph.js       the network picture shared by the home page and the instructor console
tools/bots.py            rule-based bot teammates
tools/selftest.py        end-to-end test
render.yaml              Render blueprint for the hosted copy
situsim.db               created on first run: the event log (delete it to start fresh)
```

The API is browsable at http://localhost:8000/docs.

---

## Troubleshooting

- **Other devices can't connect.** Make sure everyone is on the same Wi-Fi or hotspot and use the address the
  terminal prints. On a Mac, allow incoming connections for Python if asked. Some campus Wi-Fi blocks
  device-to-device traffic, so use a phone hotspot.
- **"Address already in use".** Another copy is running. Close it, or start on another port: `PORT=8001 python main.py`.
- **A trainee refreshed the page.** That's fine. They reconnect and get their full feed back.
- **Start over completely.** Stop the server and delete `situsim.db`.

---

## Roadmap

- AR/VR client that subscribes to the same WebSocket feed (the engine doesn't care how it is drawn)
- Scenario editor in the browser, more scenarios and difficulty curves
- Several exercises at once, instructor login
- Validating the rubric with DSSC instructors (inter-rater agreement)

---

The look is a lab notebook: paper and ink, a highlighter, tape and stickers, and a dark terminal for anything
machine-made.

Fonts: Bricolage Grotesque, Inter, JetBrains Mono and Caveat, all under the SIL Open Font License
(see `static/fonts/FONTS-OFL-LICENSE.txt`).
