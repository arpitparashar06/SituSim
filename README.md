# <p align="center"><img align="center" width="80" src="static/mark.svg"/> SituSim</p>
<h2 align="center">Multi-Domain Decision-Making Trainer for Degraded Communication Environments</h2>
<hr>
<h2 align="center">TEAM [TEAM NAME] : Smart India Hackathon 2026 (SIH 2026) 🌟</h2>
<img src="docs/screenshots/app.jpg"  width="100%"/>
<h2 align="center">COMPLETE DESCRIPTION</h2>

### PS ID : SIH26248

### Team ID : [TEAM ID]

### Organisation : Ministry of Defence, Defence Services Staff College (DSSC)

### Theme : [THEME]

### Category : Software

### PS Title :
Immersive Multi-Domain Decision-Making Trainer for Degraded Communication Environments

### PS Description :
Build a trainer in which a small team has to make decisions while their communications are degraded, and in which every decision can be reviewed afterwards:
1. Scenario Engine: Inject delay, dropout and conflicting reports into the exercise while it is running.
2. Team Coordination: Several trainees play together, each with an incomplete and degraded picture of the situation.
3. Instructor Dashboard: Configure the exercise and monitor every trainee in real time.
4. After-Action Review: An exportable review with individual and team decision timelines and the rationale behind each decision.

### Idea Title :
SituSim, a web-based trainer where every trainee sees a different, deliberately degraded picture, and every decision is judged against what that trainee could actually see.

### Idea Description :
In real operations nobody has the full picture: reports arrive late, go missing or contradict each other, and links drop. Most trainers still grade a decision against the hidden truth. SituSim grades it against what the trainee could see at that moment.
1. Degradation engine: per-trainee delays and losses, timed link outages that recover by themselves, contradicting reports, sensor outages and a spoofed source, with five fog levels.
2. Fog Director: thickens or clears the fog automatically depending on how well the team is coping.
3. Four roles, four pictures: Ground Team Lead, Air / ISR Monitor, Cyber / EW Officer and Comms Coordinator each get a different feed. They grade sources (NATO A–F / 1–6), share their read on the key questions, and reach each other by radio, relay, runner or flare (PACE).
4. Talking has a cost: enemy direction finding locates any role that transmits too much.
5. Shadow analyst: a transparent rule (no AI) reads exactly the reports each trainee had and works out what a careful reader would believe. This separates an **information gap** (fix the comms) from a **reasoning gap** (fix the person).
6. Auditable review: the After-Action Review is rebuilt only from an append-only event log, and decisions are scored on a 5-part rubric.

### Abstract/ Summary :
Command decisions are increasingly made with patchy, late or contradictory information, because of jamming, cyber effects or simply broken links. Training for this is hard: live exercises are expensive, and most simulators either give everyone the same clean picture or grade decisions against information the trainee never had.
SituSim is a browser-based trainer for small teams. Every trainee gets a different, deliberately degraded view of the same fictional scenario, the instructor controls and watches the degradation live, and the After-Action Review shows for each decision exactly what that trainee could see, so it can tell whether a mistake came from missing information or from reasoning. It runs on one laptop over local Wi-Fi with no internet, or online for remote teams.

### Status :
We have implemented these features:
1. Scenario engine with scripted and live instructor injects: report delays and losses, link outages, contradictions, sensor outages, spoofed sources, five fog levels and the automatic Fog Director.
2. Trainee screen for 4 roles: degraded report feed and map where old reports fade, live read sliders, source grading, radio / relay / runner / flare messaging, and enemy direction finding.
3. Instructor console: a live network you can click to jam, each trainee's read against the shadow analyst and the truth, a "who has what" matrix, decisions with rubric scores, injects, freeze probes (SAGAT-style) and settings.
4. After-Action Review: the picture over time (information gap vs reasoning gap), calibration and Brier scores, a "see what they saw" player, per-role lanes, decision cards with rationale, and sustain / improve findings. Exports as one offline HTML file, PDF or JSON.
5. Rule-based bot teammates to fill empty seats, and an end-to-end self-test that runs a whole exercise at 20× speed.
6. Runs fully offline on one laptop over a hotspot (fonts included), or online on Render with a free Postgres database so reviews survive restarts. Same seed and same actions give the same result (delivery fingerprint).

**Scenario: Exercise GREY LANTERN** (fictional): Blue team must get a relief convoy across the Kestrel river by T+15:00. The true report that the bridge is blocked reaches only the ISR Monitor; a spoofed report that it is open reaches only the Ground Team Lead, and arrives faster. Nobody sees both unless the team talks.

| Trainee Screen | Instructor Console |
|---|---|
| <img src="docs/screenshots/trainee.jpg"/> | <img src="docs/screenshots/instructor.jpg"/> |

**After-Action Review**

<img src="docs/screenshots/aar.jpg"  width="100%"/>

### Tech Stacks Used :
⦿ <b>BackEnd :</b>
* [![Python](https://img.shields.io/badge/python-3670A0?style=for-the-badge&logo=python&logoColor=ffdd54)](https://www.python.org/)
  [![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi)](https://fastapi.tiangolo.com/)
  [![Uvicorn](https://img.shields.io/badge/uvicorn-499848?style=for-the-badge&logo=gunicorn&logoColor=white)](https://www.uvicorn.org/)
  [![WebSockets](https://img.shields.io/badge/WebSockets-010101?style=for-the-badge&logo=socketdotio&logoColor=white)](https://developer.mozilla.org/en-US/docs/Web/API/WebSockets_API)
  [![Jinja](https://img.shields.io/badge/jinja-B41717?style=for-the-badge&logo=jinja&logoColor=white)](https://jinja.palletsprojects.com/)

⦿ <b>Database :</b>
* [![SQLite](https://img.shields.io/badge/sqlite-003B57?style=for-the-badge&logo=sqlite&logoColor=white)](https://www.sqlite.org/)
  [![PostgreSQL](https://img.shields.io/badge/postgresql-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)](https://neon.com/)

⦿ <b>FrontEnd :</b>
* [![HTML5](https://img.shields.io/badge/html5-E34F26?style=for-the-badge&logo=html5&logoColor=white)](https://developer.mozilla.org/en-US/docs/Web/HTML)
  [![CSS3](https://img.shields.io/badge/css3-1572B6?style=for-the-badge&logo=css3&logoColor=white)](https://developer.mozilla.org/en-US/docs/Web/CSS)
  [![JavaScript](https://img.shields.io/badge/javascript-323330?style=for-the-badge&logo=javascript&logoColor=F7DF1E)](https://developer.mozilla.org/en-US/docs/Web/JavaScript)

⦿ <b>Deployment :</b>
* [![Render](https://img.shields.io/badge/Render-46E3B7?style=for-the-badge&logo=render&logoColor=black)](https://render.com/)

### Important URLS :
⭐️ <b>SituSim Live Prototype :</b> [Click Here to Open]([RENDER URL])

⭐️ <b>SIH Idea PPT :</b> [Click Here to View](docs/[PPT FILE].pdf)

⭐️ <b>Youtube Video :</b> [Click Here to View]([YOUTUBE URL])

⭐️ <b>API Docs :</b> open http://localhost:8000/docs while the server is running

---

## Project Created & Maintained By

## :heart: Team [TEAM NAME]
1. [Arpit Parashar](https://github.com/arpitparashar06)
2. [NAME](https://github.com/USERNAME)
3. [NAME](https://github.com/USERNAME)
4. [NAME](https://github.com/USERNAME)
5. [NAME](https://github.com/USERNAME)
6. [NAME](https://github.com/USERNAME)

### Hire Us
<a href="https://www.linkedin.com/in/arpit-parashar06/"> <img src="https://img.shields.io/badge/arpit parashar-0077B5?style=for-the-badge&logo=linkedin&logoColor=white" alt="Connect on LinkedIn"></a>

### How-to-run

- Download Python 3.10+ from the Official website [link](https://www.python.org/downloads/) (if not installed)
- Clone this Repository.
- Run `./run.sh` on macOS / Linux or `run.bat` on Windows. The first run creates a virtual environment and installs everything.
- Open http://localhost:8000. Trainees enter a name and pick a role; the instructor opens http://localhost:8000/instructor. The terminal also prints the address other devices on the same Wi-Fi or hotspot should use.
- Not enough people? Fill the empty seats with bots: `python tools/bots.py --roles EW COMMS`.
- After the exercise press **End**, then **Open review**, or go to http://localhost:8000/aar.
- To check everything works: with the server running, `python tools/selftest.py` plays a whole exercise at 20× speed.
- Or you can directly use our application by accessing this website [link]([RENDER URL]).

### References
1. Endsley, "Measurement of Situation Awareness in Dynamic Systems," Human Factors, 1995 (SAGAT freeze probes). [Link](https://doi.org/10.1518/001872095779049499)
2. Brier, "Verification of Forecasts Expressed in Terms of Probability," Monthly Weather Review, 1950 (calibration score). [Link](https://doi.org/10.1175/1520-0493(1950)078%3C0001:VOFEIT%3E2.0.CO;2)
3. Admiralty code (NATO source reliability and information credibility grading, A–F / 1–6). [Link](https://en.wikipedia.org/wiki/Admiralty_code)
4. After-action review. [Link](https://en.wikipedia.org/wiki/After-action_review)

Everything in the scenario is fictional (Kestrel Valley, Blue team, "unidentified group"). EW and cyber appear only as abstract effects on information: delay, loss and spoofing. No real units, places, equipment or techniques.
Fonts: Bricolage Grotesque, Inter, JetBrains Mono and Caveat, under the SIL Open Font License (`static/fonts/FONTS-OFL-LICENSE.txt`).

## Support

💙 If you like this project, give it a '⭐' and share it with your friends!
You are free to send us PRs and issues, We'd love to help and improve this.

<h1 align="center">🙏 THANK YOU 🙏</h1>

![Typing SVG](https://readme-typing-svg.demolab.com?font=Oldenburg&color=5B4FFF&lines=Best+Wishes+from+Team+[TEAM+NAME])
