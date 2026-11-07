"""Machine briefs. Training set teaches the agent; held-out set proves it learned."""

TRAINING = [
    {"id": "cargo-drone", "name": "Cargo Drone",
     "prompt": "A delivery drone that carries a 6 kg parcel for 25 minutes and fits through a 1.6 m gate.",
     "payload_kg": 6, "endurance_min": 25, "max_span_m": 1.6},
    {"id": "hoverboard", "name": "Hoverboard",
     "prompt": "A hoverboard that lifts an 80 kg rider for 4 minutes, no wider than 1.6 m, safe downwash.",
     "payload_kg": 80, "endurance_min": 4, "max_span_m": 1.6, "max_downwash_m_s": 30},
    {"id": "wing-pack", "name": "Wing Pack",
     "prompt": "Wearable wings that lift a 75 kg person for 5 minutes, span up to 3 m.",
     "payload_kg": 75, "endurance_min": 5, "max_span_m": 3.0, "max_mass_kg": 160},
    {"id": "camera-drone", "name": "Camera Drone",
     "prompt": "A cinema drone that carries a 2.5 kg camera for 35 minutes, max 1.2 m wide, under 9 kg total.",
     "payload_kg": 2.5, "endurance_min": 35, "max_span_m": 1.2, "max_mass_kg": 9},
]

HELD_OUT = [
    {"id": "jetpack", "name": "Jetpack",
     "prompt": "A jetpack that lifts an 85 kg pilot for 4 minutes, no wider than 2.0 m, under 150 kg total.",
     "payload_kg": 85, "endurance_min": 4, "max_span_m": 2.0, "max_mass_kg": 150},
    {"id": "rescue-drone", "name": "Rescue Drone",
     "prompt": "A rescue drone that carries a 15 kg medical kit for 20 minutes, max 2.2 m wide.",
     "payload_kg": 15, "endurance_min": 20, "max_span_m": 2.2},
]

DEMO = [
    {"id": "ironman", "name": "Iron Man Suit",
     "prompt": "A personal flying suit that lifts a 90 kg human for 5 minutes, no wider than 2.0 m, under 170 kg total.",
     "payload_kg": 90, "endurance_min": 5, "max_span_m": 2.0, "max_mass_kg": 170},
    {"id": "pizza-storm", "name": "Storm Pizza Drone",
     "prompt": "Deliver a pizza from San Francisco to Fremont (45 km) through hurricane-like 25 m/s winds and heavy rain.",
     "payload_kg": 2.0, "max_span_m": 2.5, "distance_km": 45, "wind_m_s": 25, "rain": True},
]

ALL = {b["id"]: b for b in TRAINING + HELD_OUT + DEMO}
