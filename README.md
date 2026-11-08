# AdaptMach

**An AI engineer that designs machines in code, and teaches itself physics from its own failures.**

Built at the AIE Code Summit 2026 Hackathon (Cerebral Valley · Shack15), Nov 7 2026.
Problem statement: **The System That Gets Better**.

## What it does

You describe a machine: *"a jetpack that lifts an 85 kg pilot for 4 minutes, under 150 kg."*

1. **Design by code.** The agent writes a Python `design(brief, tools)` function that picks rotors, motors, battery chemistry, frame and ducts.
2. **Physics grades every attempt.** A coupled first-principles simulator (momentum theory, figure of merit, duct augmentation, battery specific energy and C-rate limits, mass build-up) checks lift margin, endurance, battery power, motor headroom, size, downwash, mass, thrust and power caps. Only 0.1–2% of designs in the search space pass.
3. **Failures become skills.** After each training job, a learning model reviews the session and writes ONE new versioned skill: either a lesson or a **Python tool** the design code can call (e.g. its own hover-power estimator).
4. **Skills must prove themselves on held-out work.** Each candidate skill is tested on machines the agent has never trained on (Jetpack, Rescue Drone). It is **adopted** only if average attempts-to-valid-design go down. Otherwise it is **rolled back**.
5. **Adapt.** Take any working machine and ask for a change (*"cut 20 kg and reduce thrust by 35%"*). AdaptMach turns that into new physical constraints and redesigns the machine using everything it has learned.

## What was built during the hackathon

Everything in this repo was written on Nov 7, 2026:
- `physics.py`: coupled vertical-lift physics simulator + checks
- `briefs.py`: 4 training machines, 2 held-out machines
- `agent.py`: design loop, sandboxed code execution, reflection into versioned skills, held-out evaluation, adopt/rollback, adapt mode
- `sandbox_runner.py`: isolated subprocess that runs agent-written code with restricted builtins
- `llm.py`: stdlib OpenAI-compatible client (Meta Model API by default)
- `server.py` + `static/index.html`: live engineering feed, versioned skill library, held-out improvement curve, run replay

No external Python packages are needed.

## Run it

```bash
bash start.sh          # asks which model + your key, opens http://localhost:8000
```

Or set the environment yourself:

```bash
export MODEL_API_KEY=...                 # Meta Model API key (dev.meta.ai)
# optional: a separate model for the learning step (e.g. Subquadratic)
export REFLECT_API_KEY=... REFLECT_BASE_URL=... REFLECT_MODEL=...
python3 server.py
```

Any OpenAI-compatible endpoint works via `MODEL_BASE_URL` / `MODEL_NAME`.
`ADAPTMACH_MOCK=1` runs an offline scripted brain for testing only (clearly labelled MOCK in the UI).

Every run is saved to `runs/` and can be replayed from the UI.

## Demo

Open http://localhost:8000 and press **▶ Play demo**: a ~2.5 minute guided replay of a recorded live run
(every design was written by `muse-spark-1.3`, every verdict comes from `physics.py`):

1. **Hurricane drone.** The day-0 agent designs a drone to carry a pizza San Francisco → Fremont (45 km, 25 m/s winds, rain). Each attempt flies in a Three.js 3D storm, and the physics result decides what happens: out of battery over the bay, blown backwards, flipped by a gust, or delivered.
2. **What it learned.** The agent reviews the drone's crashes and writes a lesson or a Python tool.
3. **Teaching other machines.** The lesson is tested live on machines it never trained on (a Mars jetpack and a storm supply drone). It is kept only if they need fewer attempts. Otherwise it is rolled back.
4. **Before vs after.** The day-0 agent and the trained agent race to build a new machine (an Iron Man suit), shown as a 3D model drawn from each design.

Everything is also live: the mission and prototyping buttons call the model in real time (about 25 s per design iteration).

Notes: `muse-spark-1.3` is a reasoning model, so `llm.py` sends `reasoning_effort: "low"` (at default effort it spent the whole token budget thinking and returned empty content).
