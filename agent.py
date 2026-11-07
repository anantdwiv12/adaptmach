"""
AdaptMach agent: an autonomous engineer that writes design code, tests it
against physics, and turns its own failures into versioned skills that are
adopted only if they improve performance on held-out machines.
"""
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import physics
import llm
from briefs import TRAINING, HELD_OUT

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", "6"))
FAIL_PENALTY = MAX_ATTEMPTS + 2   # attempts charged for an unsolved machine

# --------------------------------------------------------------------------- prompts

DESIGN_SYSTEM = """You are AdaptMach, an autonomous machine-design engineer.
You design electric vertical-lift machines (drones, hoverboards, wing packs, jetpacks)
by WRITING PYTHON CODE.

Write exactly one Python code block that defines:

    def design(brief, tools):
        ...
        return {{...}}

`brief` is a dict with keys like payload_kg, endurance_min, max_span_m and optional limits
(max_mass_kg, max_downwash_m_s, max_thrust_kn, max_motor_power_kw). It may also set
planet ("earth", "mars", "titan"), gravity (m/s^2) and air_density (kg/m^3). Earth: 9.81, 1.225.
Mars: 3.71, 0.020 (thin air: rotors need far more disk area). Titan: 1.35, 5.40 (dense air, easy lift).
Mission briefs may set distance_km, wind_m_s and rain. Then also choose "cruise_speed_m_s": the sim models
forward-flight power (induced + parasite drag ~ v^3), ground speed into the route-averaged headwind
(0.7 x wind), trip energy incl. 2 min hover and 15% reserve, gust stability (thrust/weight >= 1.5 + wind/40),
motor headroom vs cruise power, and rain (+7% waterproofing mass, +15% drag).
`tools` is a dict of helper functions you wrote earlier (your learned tools); call them as
tools["name"](...). `math` is available. No imports.

design() must return a dict with these keys (cruise_speed_m_s only matters for missions):
{keys}

A physics simulator will test your design against the brief: thrust-to-weight >= 1.5,
endurance (if given), mission range (if given), battery power (C-rate) >= 1.5x hover power, motor headroom >= 1.5x hover power,
span limit, plus any optional limits. Everything is coupled: battery mass raises total mass,
which raises hover power (P ~ W^1.5 / sqrt(rotor disk area)), which cuts endurance.

{skills}
Return only the code block."""

REFLECT_SYSTEM = """You are the learning module of AdaptMach, an autonomous machine-design engineer.
You review a design session (attempts + physics-simulator feedback) and distil ONE reusable skill
that would let the engineer reach a valid design in FEWER attempts on DIFFERENT, unseen machines.

A skill is either:
- a "lesson": a short, general, actionable rule (1-3 sentences, with numbers if useful), or
- a "tool": a pure Python function (math available, no imports) that the design code can call,
  e.g. an estimator that sizes something from first principles.

Do not repeat existing skills. Prefer general physics/engineering insight over machine-specific
values. Respond with JSON only:
{"name": "snake_case_name", "kind": "lesson" | "tool",
 "description": "one line: what it does and when to use it",
 "content": "the lesson text, or the full Python source defining a function named exactly `name`"}"""

ADAPT_SYSTEM = """You translate an engineer's change request for an existing machine into updated
requirement fields. Current brief and current physics report are given. Allowed fields:
payload_kg, endurance_min, max_span_m, max_mass_kg, max_downwash_m_s, max_thrust_kn,
max_motor_power_kw, planet ("earth" | "mars" | "titan"). When moving to another planet, set planet
and relax limits that only made sense on Earth (e.g. on Mars allow max_span_m up to ~8 m and drop
max_mass_kg unless the user asked for it; use null to remove a limit). Use numbers from the current report to turn relative requests into absolute
limits (e.g. "cut 20 kg" -> max_mass_kg = current mass - 20; "reduce thrust by 35%" ->
max_thrust_kn = current max thrust * 0.65). Respond with JSON only:
{"changes": {field: number, ...}, "summary": "one line in plain English"}"""


IMAGINE_SYSTEM = """You turn a plain-English idea for a NEW machine into a design brief for an
electric vertical-lift machine (rotors/fans + battery). Choose realistic, challenging but achievable
numbers. Respond with JSON only:
{"name": "short catchy machine name", "prompt": "one-sentence restatement of the request",
 "payload_kg": number, "endurance_min": number or null, "max_span_m": number,
 "max_mass_kg": number or null, "max_downwash_m_s": number or null,
 "distance_km": number or null, "wind_m_s": number or null, "rain": true | false,
 "planet": "earth" | "mars" | "titan"}
For delivery / travel missions set distance_km (straight-line), wind_m_s (storm ~15, severe storm ~20,
hurricane-like ~25) and rain, and leave endurance_min null.
Guidance: a human pilot ~80-95 kg; things near people should keep downwash <= 30 m/s;
on Mars allow spans of 4-8 m; keep endurance modest for human-carrying machines (3-10 min)."""


def skills_block(library):
    if not library:
        return "You have no learned skills yet.\n"
    lines = ["YOUR LEARNED SKILLS (from previous jobs; use them):"]
    for s in library:
        if s["kind"] == "lesson":
            lines.append(f"- LESSON {s['name']}: {s['content']}")
        else:
            lines.append(f"- TOOL tools[\"{s['name']}\"]: {s['description']}\n```python\n{s['content']}\n```")
    return "\n".join(lines) + "\n"


def history_block(attempts):
    if not attempts:
        return "This is your first attempt."
    out = ["Previous attempts and simulator feedback:"]
    for a in attempts[-4:]:
        out.append(f"\n--- Attempt {a['n']} ---")
        if a.get("error"):
            out.append(f"Your code crashed: {a['error']}")
            continue
        out.append("design = " + json.dumps(a["design"]))
        out.append("report = " + json.dumps(a["sim"]["report"]))
        for c in a["sim"]["checks"]:
            out.append(("PASS " if c["ok"] else "FAIL ") + f"{c['name']}: {c['detail']}")
    return "\n".join(out)


def brief_block(brief):
    fields = {k: v for k, v in brief.items() if k not in ("id", "name", "prompt")}
    return f"MACHINE: {brief['name']}\nREQUEST: {brief['prompt']}\nbrief = {json.dumps(fields)}"


# --------------------------------------------------------------------------- sandbox

def run_design_code(code, tools, brief):
    req = json.dumps({"code": code, "tools": tools, "brief": brief})
    try:
        p = subprocess.run([sys.executable, "-I", os.path.join(HERE, "sandbox_runner.py")],
                           input=req, capture_output=True, text=True, timeout=8)
        out = json.loads(p.stdout.strip().splitlines()[-1])
    except subprocess.TimeoutExpired:
        return None, "code timed out (8s)"
    except Exception as e:
        return None, f"sandbox failure: {e}"
    if "error" in out:
        return None, out["error"]
    return out["design"], None


def validate_tool(skill):
    code = "def design(brief, tools):\n    return {'ok': callable(tools.get(%r))}\n" % skill["name"]
    d, err = run_design_code(code, [skill], {})
    return bool(d and d.get("ok")), err


# --------------------------------------------------------------------------- mock brain
# Deterministic stand-in for the LLM so the whole pipeline can be tested offline.
# It is clearly labelled MOCK in the UI. Real runs use the Meta Model API.

class MockBrain:
    label = "MOCK (offline test brain)"

    def design_code(self, brief, attempts, library):
        time.sleep(float(os.environ.get("MOCK_DELAY", "0.4")))
        names = {s["name"] for s in library}
        last = attempts[-1]["design"] if attempts and attempts[-1].get("design") else None
        if last is None:
            d = {"n_rotors": 4, "rotor_diameter_m": 0.4, "motor_power_kw": 1.0,
                 "battery_kwh": 0.5, "battery_chemistry": "li-ion",
                 "frame_material": "aluminum", "ducted": False}
            if "prefer_carbon_ducts" in names:
                d.update(frame_material="carbon", ducted=True)
            if "size_from_hover_power" in names:
                w = (brief["payload_kg"] * 1.7) * 9.81
                d["rotor_diameter_m"] = round(min(1.2, max(0.3, brief["max_span_m"] / 2.6)), 2)
                area = 4 * 3.1416 * d["rotor_diameter_m"] ** 2 / 4
                ph = (w / 1.26) ** 1.5 / (2 * 1.225 * area) ** 0.5 / 0.8 / 0.85 / 1000
                d["motor_power_kw"] = max(0.3, round(ph * 1.9 / 4, 2))
                d["battery_kwh"] = max(0.3, round(ph * (brief.get("endurance_min") or 30) / 60 / 0.8 * 1.3, 2))
            if "chemistry_by_c_rate" in names and (brief.get("endurance_min") or 99) <= 8:
                d["battery_chemistry"] = "li-po"
        else:
            d = dict(last)
            fails = {c["name"] for c in attempts[-1]["sim"]["checks"] if not c["ok"]}
            if "lift_margin" in fails or "motor_headroom" in fails:
                d["motor_power_kw"] = round(d["motor_power_kw"] * 1.8, 2)
            if "battery_power" in fails:
                d["battery_chemistry"] = "li-po"
            if "headway" in fails:
                d["cruise_speed_m_s"] = d.get("cruise_speed_m_s", 15) + 8
            if "mission_energy" in fails:
                d["battery_kwh"] = round(d["battery_kwh"] * 1.5, 2)
                d["battery_chemistry"] = "li-s"
                d["rotor_diameter_m"] = min(1.0, round(d["rotor_diameter_m"] * 1.2, 2))
            if "endurance" in fails:
                d["battery_kwh"] = round(d["battery_kwh"] * 1.6, 2)
            if "size_limit" in fails:
                d["rotor_diameter_m"] = round(d["rotor_diameter_m"] * 0.8, 2)
                d["ducted"] = True
            if "downwash_safety" in fails:
                d["n_rotors"] = min(16, d["n_rotors"] + 2)
            if "mass_limit" in fails:
                d["frame_material"] = "carbon"
                d["battery_kwh"] = round(d["battery_kwh"] * 0.85, 2)
        body = ",\n        ".join(f"{k!r}: {v!r}" for k, v in d.items())
        return f"def design(brief, tools):\n    return {{\n        {body}\n    }}"

    _queue = [
        {"name": "size_from_hover_power", "kind": "lesson",
         "description": "Size motors and battery from momentum-theory hover power before guessing.",
         "content": "Estimate hover power first (P = W^1.5/sqrt(2*rho*A)/FM/eta), then set motors to ~1.9x hover and battery to hover*time*1.3/0.8."},
        {"name": "more_rotors_always", "kind": "lesson",
         "description": "Always use 16 rotors.",
         "content": "Use 16 rotors on every design."},
        {"name": "prefer_carbon_ducts", "kind": "lesson",
         "description": "Carbon frame + ducts give the best lift per kg in tight spans.",
         "content": "Default to carbon frames and ducted rotors when span is constrained."},
        {"name": "chemistry_by_c_rate", "kind": "lesson",
         "description": "Short, high-power missions need li-po for C-rate.",
         "content": "For missions under ~8 minutes use li-po: li-ion cannot deliver the burst power."},
    ]

    def reflect(self, brief, attempts, library, rejected=()):
        have = {s["name"] for s in list(library) + list(rejected)}
        for s in self._queue:
            if s["name"] not in have:
                return dict(s)
        return None

    def adapt(self, brief, report, instruction):
        low = instruction.lower()
        for planet in ("mars", "titan", "earth"):
            if planet in low:
                ch = {"planet": planet, "max_mass_kg": None}
                if planet == "mars":
                    ch["max_span_m"] = 8
                return {"changes": ch, "summary": f"Move the machine to {planet.title()}."}
        return {"changes": {"max_mass_kg": round(report["mass_kg"] - 20, 1)},
                "summary": "Cap total mass 20 kg below the current design."}

    def imagine(self, text, planet):
        return {"name": text[:40].title(), "prompt": text, "payload_kg": 40,
                "endurance_min": 8, "max_span_m": 2.5, "planet": planet or "earth"}


class LLMBrain:
    def __init__(self, builder, reflector):
        self.builder = builder
        self.reflector = reflector
        self.label = f"LIVE · builder: {builder.label} ({builder.model}) · learner: {reflector.label} ({reflector.model})"

    def design_code(self, brief, attempts, library):
        system = DESIGN_SYSTEM.format(
            keys="\n".join(f"  {k}: {v}" for k, v in physics.DESIGN_KEYS.items()),
            skills=skills_block(library))
        user = brief_block(brief) + "\n\n" + history_block(attempts)
        return llm.extract_code(self.builder.chat(system, user))

    def reflect(self, brief, attempts, library, rejected=()):
        existing = "\n".join(f"- {s['name']}: {s['description']}" for s in library) or "(none)"
        if rejected:
            existing += "\nREJECTED BEFORE (did not help on held-out machines, do not repeat):\n" + \
                "\n".join(f"- {s['name']}: {s['description']}" for s in rejected)
        user = (brief_block(brief) + "\n\nEXISTING SKILLS:\n" + existing +
                "\n\nSESSION:\n" + history_block(attempts))
        s = llm.extract_json(self.reflector.chat(REFLECT_SYSTEM, user))
        if not {"name", "kind", "content"} <= set(s):
            return None
        s.setdefault("description", s["name"])
        s["kind"] = "tool" if s["kind"] == "tool" else "lesson"
        return s

    def imagine(self, text, planet):
        user = f"IDEA: {text}" + (f"\nPLANET: {planet}" if planet else "")
        return llm.extract_json(self.builder.chat(IMAGINE_SYSTEM, user))

    def adapt(self, brief, report, instruction):
        user = (f"CURRENT BRIEF: {json.dumps(brief)}\nCURRENT REPORT: {json.dumps(report)}\n"
                f"CHANGE REQUEST: {instruction}")
        return llm.extract_json(self.builder.chat(ADAPT_SYSTEM, user))


def make_brain():
    if os.environ.get("ADAPTMACH_MOCK") == "1":
        return MockBrain()
    b = llm.builder_client()
    if not b:
        return MockBrain()
    return LLMBrain(b, llm.reflector_client())


# --------------------------------------------------------------------------- engine

class Engine:
    def __init__(self):
        self.lock = threading.Lock()
        self.brain = make_brain()
        self.reset()

    def reset(self):
        self.events = []
        self.library = []          # adopted skills
        self.version = 0
        self.versions = []         # [{version, mean, per_brief, skills}]
        self.rejected = []
        self.solved = {}           # brief_id -> {brief, design, report}
        self.running = False
        self.run_id = time.strftime("%Y%m%d-%H%M%S")

    # ---- events
    def emit(self, type_, **data):
        with self.lock:
            ev = {"i": len(self.events), "t": round(time.time(), 2), "type": type_, **data}
            self.events.append(ev)
        return ev

    def snapshot(self, since=0):
        with self.lock:
            return {"events": self.events[since:], "total": len(self.events),
                    "library": self.library, "version": self.version,
                    "versions": self.versions, "rejected": self.rejected,
                    "running": self.running, "brain": self.brain.label,
                    "solved": {k: {"name": v["brief"]["name"], "report": v["report"],
                                   "design": v.get("design"), "brief": v["brief"]}
                               for k, v in self.solved.items()},
                    "run_id": self.run_id}

    def save(self):
        os.makedirs(os.path.join(HERE, "runs"), exist_ok=True)
        snap = self.snapshot()
        with self.lock:
            snap["solved_full"] = self.solved
        with open(os.path.join(HERE, "runs", f"{self.run_id}.json"), "w") as f:
            json.dump(snap, f)

    def load_latest_brain(self):
        """Restore the most recent trained skill library (so a restart keeps the trained agent)."""
        d = os.path.join(HERE, "runs")
        if not os.path.isdir(d):
            return False
        for name in sorted(os.listdir(d), reverse=True):
            try:
                snap = json.load(open(os.path.join(d, name)))
            except Exception:
                continue
            if str(snap.get("brain", "")).startswith("MOCK") != self.brain.label.startswith("MOCK"):
                continue   # never mix offline test runs with live runs
            if snap.get("library") or snap.get("rejected"):
                self.library = snap.get("library", [])
                self.version = snap.get("version", 0)
                self.versions = snap.get("versions", [])
                self.rejected = snap.get("rejected", [])
                self.solved = snap.get("solved_full", {})
                # replay the learning milestones so the UI can redraw the improvement curve
                for ev in snap.get("events", []):
                    if ev.get("type") in ("baseline", "adopt", "rollback", "run_end"):
                        self.emit(ev["type"], **{k: v for k, v in ev.items() if k not in ("i", "t", "type")})
                self.emit("note", text=f"Loaded trained agent v{self.version} "
                          f"({len(self.library)} skills kept, {len(self.rejected)} rolled back) "
                          f"from run {name[:-5]}")
                return True
        return False

    # ---- one design job
    def solve(self, brief, library, phase, seed_attempts=None, race=None, lane=None, stop=None,
              version=None):
        tid = uuid.uuid4().hex[:8]
        if version is None:
            version = self.version if phase != "candidate" else self.version + 1
        self.emit("task_start", task=tid, phase=phase, brief=brief, version=version,
                  skills=[s["name"] for s in library], race=race, lane=lane)
        tools = [s for s in library if s["kind"] == "tool"]
        attempts = list(seed_attempts or [])
        limit = MAX_ATTEMPTS if phase in ("baseline", "candidate", "training") else MAX_ATTEMPTS + 4
        for n in range(1, limit + 1):
            if stop is not None and stop.is_set():
                self.emit("task_end", task=tid, solved=False, attempts=n - 1, stopped=True)
                return {"solved": False, "attempts": FAIL_PENALTY, "trace": attempts, "stopped": True}
            a = {"n": n}
            try:
                code = self.brain.design_code(brief, attempts, library)
            except Exception as e:
                a["error"] = f"model error: {e}"
                attempts.append(a)
                self.emit("attempt", task=tid, n=n, error=a["error"], code="")
                continue
            design, err = run_design_code(code, tools, brief)
            a["code"] = code
            if err:
                a["error"] = err
                attempts.append(a)
                self.emit("attempt", task=tid, n=n, code=code, error=err)
                continue
            sim = physics.safe_simulate(design, brief)
            a.update(design=design, sim=sim)
            attempts.append(a)
            self.emit("attempt", task=tid, n=n, code=code, design=design, sim=sim)
            if sim["ok"]:
                self.emit("task_end", task=tid, solved=True, attempts=n)
                return {"solved": True, "attempts": n, "trace": attempts,
                        "design": design, "report": sim["report"]}
        self.emit("task_end", task=tid, solved=False, attempts=limit)
        return {"solved": False, "attempts": FAIL_PENALTY, "trace": attempts}

    def evaluate(self, library, phase):
        with ThreadPoolExecutor(max_workers=len(HELD_OUT)) as ex:
            results = list(ex.map(lambda b: self.solve(b, library, phase), HELD_OUT))
        per = {b["id"]: r["attempts"] for b, r in zip(HELD_OUT, results)}
        mean = sum(per.values()) / len(per)
        return mean, per

    # ---- the self-improvement loop
    def learn(self, cycles=4):
        self.running = True
        try:
            self.emit("run_start", brain=self.brain.label, cycles=cycles,
                      training=[b["name"] for b in TRAINING], heldout=[b["name"] for b in HELD_OUT])
            mean, per = self.evaluate(self.library, "baseline")
            self.versions.append({"version": 0, "mean": mean, "per_brief": per, "skills": []})
            self.emit("baseline", version=0, mean=mean, per_brief=per)
            current = mean
            for c in range(cycles):
                brief = TRAINING[c % len(TRAINING)]
                res = self.solve(brief, self.library, "training")
                if res["solved"]:
                    self.solved[brief["id"]] = {"brief": brief, "design": res["design"], "report": res["report"]}
                self.emit("reflecting", brief=brief["name"])
                try:
                    skill = self.brain.reflect(brief, res["trace"], self.library, self.rejected)
                except Exception as e:
                    self.emit("note", text=f"reflection failed: {e}")
                    continue
                if not skill:
                    self.emit("note", text="no new skill proposed")
                    continue
                if any(s["name"] == skill["name"] for s in self.library):
                    skill["name"] += f"_{c}"
                if skill["kind"] == "tool":
                    ok, err = validate_tool(skill)
                    if not ok:
                        self.rejected.append({**skill, "reason": f"tool failed to load: {err}"})
                        self.emit("rollback", skill=skill, reason=f"tool failed to load: {err}")
                        continue
                self.emit("candidate", skill=skill, version=self.version + 1)
                cand_mean, cand_per = self.evaluate(self.library + [skill], "candidate")
                if cand_mean < current:
                    self.library.append({**skill, "version": self.version + 1,
                                         "evidence": f"held-out avg {current:.1f} -> {cand_mean:.1f} attempts"})
                    self.version += 1
                    self.versions.append({"version": self.version, "mean": cand_mean,
                                          "per_brief": cand_per,
                                          "skills": [s["name"] for s in self.library]})
                    self.emit("adopt", skill=skill, version=self.version,
                              before=current, after=cand_mean, per_brief=cand_per)
                    current = cand_mean
                else:
                    reason = f"held-out avg {current:.1f} -> {cand_mean:.1f} attempts (no improvement)"
                    self.rejected.append({**skill, "reason": reason})
                    self.emit("rollback", skill=skill, reason=reason, before=current,
                              after=cand_mean, per_brief=cand_per)
                self.save()
            self.emit("run_end", baseline=self.versions[0]["mean"], final=current,
                      version=self.version)
        except Exception as e:
            self.emit("note", text=f"run crashed: {e}")
        finally:
            self.running = False
            self.save()

    # ---- adapt an existing machine to a change request
    def adapt(self, brief_id, instruction):
        self.running = True
        try:
            base = self.solved[brief_id]
            self.emit("adapt_start", brief=base["brief"]["name"], instruction=instruction)
            spec = self.brain.adapt(base["brief"], base["report"], instruction)
            new_brief = dict(base["brief"])
            for k, v in (spec.get("changes") or {}).items():
                if v is None:
                    new_brief.pop(k, None)
                elif k == "planet":
                    new_brief["planet"] = str(v).lower()
                else:
                    try:
                        new_brief[k] = float(v)
                    except (TypeError, ValueError):
                        pass
            new_brief["name"] = base["brief"]["name"] + " (adapted)"
            new_brief["prompt"] = instruction
            self.emit("adapt_spec", summary=spec.get("summary", ""), changes=spec.get("changes", {}))
            seed = [{"n": 0, "design": base["design"],
                     "sim": physics.safe_simulate(base["design"], new_brief)}]
            res = self.solve(new_brief, self.library, "adapt", seed_attempts=seed)
            if res["solved"]:
                self.solved[brief_id + "-adapted"] = {"brief": new_brief, "design": res["design"],
                                                      "report": res["report"]}
            self.emit("adapt_end", solved=res["solved"])
        except Exception as e:
            self.emit("note", text=f"adapt failed: {e}")
        finally:
            self.running = False
            self.save()

    # ---- design a machine described in plain English
    def _brief_from_text(self, text, planet):
        low = text.lower()
        if "pizza" in low and "fremont" in low:      # demo preset: known-hard but physically solvable
            from briefs import ALL
            b = dict(ALL["pizza-storm"])
            b["id"] = "pizza-" + uuid.uuid4().hex[:5]
            return b
        spec = self.brain.imagine(text, planet)
        brief = {"id": "custom-" + uuid.uuid4().hex[:5], "name": spec.get("name") or "Custom machine",
                 "prompt": spec.get("prompt") or text}
        for k in ("payload_kg", "endurance_min", "max_span_m", "max_mass_kg", "max_downwash_m_s",
                  "distance_km", "wind_m_s"):
            v = spec.get(k)
            if v is not None:
                try:
                    brief[k] = float(v)
                except (TypeError, ValueError):
                    pass
        if spec.get("rain"):
            brief["rain"] = True
        brief.setdefault("payload_kg", 50.0)
        if not brief.get("distance_km"):
            brief.setdefault("endurance_min", 5.0)
        brief.setdefault("max_span_m", 3.0)
        brief["planet"] = str(planet or spec.get("planet") or "earth").lower()
        return brief

    def imagine(self, text, planet=None, brief_id=None):
        self.running = True
        try:
            if brief_id:                                # preset machine, e.g. the Iron Man suit
                from briefs import ALL
                brief = dict(ALL[brief_id])
                brief["id"] = brief_id + "-" + uuid.uuid4().hex[:5]
                if planet:
                    brief["planet"] = planet
                text = text or brief["prompt"]
            self.emit("imagine_start", text=text, planet=planet or "earth")
            if not brief_id:
                brief = self._brief_from_text(text, planet)
            self.emit("imagine_spec", brief=brief)
            res = self.solve(brief, self.library, "imagine")
            if res["solved"]:
                self.solved[brief["id"]] = {"brief": brief, "design": res["design"], "report": res["report"]}
        except Exception as e:
            self.emit("note", text=f"imagine failed: {e}")
        finally:
            self.running = False
            self.save()

    # ---- race: untrained agent (no skills) vs trained agent, same never-seen machine
    def race(self, brief_id=None, text=None, planet=None):
        self.running = True
        try:
            if text:
                brief = self._brief_from_text(text, planet)
            else:
                from briefs import ALL
                brief = dict(ALL[brief_id or "ironman"])
                if planet:
                    brief["planet"] = planet
            rid = uuid.uuid4().hex[:6]
            self.emit("race_start", race=rid, brief=brief, trained_version=self.version,
                      skills=[s["name"] for s in self.library])
            stop = threading.Event()
            results = {}

            def run(lane):
                lib = [] if lane == "untrained" else self.library
                ver = 0 if lane == "untrained" else self.version
                r = self.solve(brief, lib, "race", race=rid, lane=lane, stop=stop, version=ver)
                results[lane] = r
                if r["solved"] and not stop.is_set():
                    stop.set()
                    self.emit("race_winner", race=rid, lane=lane, attempts=r["attempts"])
                    if lane == "trained":
                        self.solved[brief["id"]] = {"brief": brief, "design": r["design"],
                                                    "report": r["report"]}
            ts = [threading.Thread(target=run, args=(l,)) for l in ("untrained", "trained")]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            if not any(r.get("solved") for r in results.values()):
                self.emit("race_winner", race=rid, lane=None, attempts=None)
        except Exception as e:
            self.emit("note", text=f"race failed: {e}")
        finally:
            self.running = False
            self.save()
