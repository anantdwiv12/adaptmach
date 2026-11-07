"""
Imagimach physics engine.

A coupled first-principles model of an electric vertical-lift machine
(drones, hoverboards, wing packs, jetpacks). Everything feeds back into
everything: more battery -> more mass -> more hover power -> less endurance.
That coupling is what makes design hard, and what the agent must learn.

Model: actuator-disk (momentum) theory for rotors, figure of merit for
real-rotor losses, duct augmentation, battery specific energy / C-rate
limits, and mass build-up from components.
"""
import math

RHO = 1.225      # air density, kg/m^3
G = 9.81         # gravity, m/s^2
ETA_MOTOR = 0.85 # motor + ESC efficiency
USABLE_BATTERY = 0.80

PLANETS = {
    #          gravity m/s^2, air density kg/m^3
    "earth": (9.81, 1.225),
    "mars":  (3.71, 0.020),
    "titan": (1.35, 5.40),
}

BATTERIES = {
    #            Wh/kg  max continuous C-rate
    "li-ion":   (250,   4.0),
    "li-po":    (170,   25.0),
    "li-s":     (380,   1.5),
}
FRAMES = {
    #             structural mass fraction of carried mass
    "carbon":    0.10,
    "aluminum":  0.18,
    "steel":     0.30,
}

DESIGN_KEYS = {
    "n_rotors": "int, number of rotors / fans (2-16)",
    "rotor_diameter_m": "float, diameter of each rotor in metres (0.1-4.0)",
    "motor_power_kw": "float, rated power of EACH motor in kW",
    "battery_kwh": "float, total battery energy in kWh",
    "battery_chemistry": "one of: " + ", ".join(BATTERIES),
    "frame_material": "one of: " + ", ".join(FRAMES),
    "ducted": "bool, rotors inside ducts (more efficient, heavier, smaller span)",
    "cruise_speed_m_s": "float, airspeed for missions with distance_km (5-60). Ignored for hover-only briefs",
}


class DesignError(Exception):
    pass


def _num(d, k, lo, hi, kind=float):
    if k not in d:
        raise DesignError(f"missing field '{k}'")
    try:
        v = kind(d[k])
    except Exception:
        raise DesignError(f"field '{k}' must be a number")
    if not (lo <= v <= hi):
        raise DesignError(f"field '{k}'={v} out of range [{lo}, {hi}]")
    return v


def simulate(design: dict, brief: dict) -> dict:
    """Return a full physics report + pass/fail checks for a design vs a brief."""
    n = _num(design, "n_rotors", 2, 16, int)
    D = _num(design, "rotor_diameter_m", 0.1, 4.0)
    p_motor = _num(design, "motor_power_kw", 0.05, 200.0)
    e_kwh = _num(design, "battery_kwh", 0.01, 50.0)
    chem = design.get("battery_chemistry")
    if chem not in BATTERIES:
        raise DesignError(f"battery_chemistry must be one of {list(BATTERIES)}")
    frame = design.get("frame_material")
    if frame not in FRAMES:
        raise DesignError(f"frame_material must be one of {list(FRAMES)}")
    ducted = bool(design.get("ducted", False))

    wh_kg, c_rate = BATTERIES[chem]
    payload = brief["payload_kg"]
    planet = str(brief.get("planet", "earth")).lower()
    g_default, rho_default = PLANETS.get(planet, PLANETS["earth"])
    G = float(brief.get("gravity", g_default))
    RHO = float(brief.get("air_density", rho_default))

    # ---- mass build-up ----
    m_battery = e_kwh * 1000.0 / wh_kg
    m_motors = n * (0.15 * p_motor + 0.05)                 # ~6.7 kW/kg incl. ESC
    m_rotors = n * 0.35 * D ** 2                           # blades + hub
    m_ducts = n * (1.1 * D ** 1.5) if ducted else 0.0      # duct shells
    carried = payload + m_battery + m_motors + m_rotors + m_ducts
    m_frame = FRAMES[frame] * carried + 0.4 * n * D        # arms scale with span
    rain = bool(brief.get("rain", False))
    m_seal = 0.07 * (carried + m_frame) if rain else 0.0   # waterproofing: sealed motors, covers
    mass = carried + m_frame + m_seal
    weight = mass * G

    # ---- geometry ----
    area = n * math.pi * D ** 2 / 4.0
    spacing = 1.05 if ducted else 1.25                     # rotor-to-rotor gap
    if n <= 2:
        span = n * D * spacing
    else:
        ring_r = (D * spacing) / (2 * math.sin(math.pi / n))
        span = 2 * ring_r + D

    # ---- aerodynamics (momentum theory) ----
    fm = 0.80 if ducted else 0.65
    thrust_gain = 1.26 if ducted else 1.0                  # duct thrust augmentation
    def power_for_thrust(T):
        return (T / thrust_gain) ** 1.5 / math.sqrt(2 * RHO * area) / fm / ETA_MOTOR
    p_hover = power_for_thrust(weight)                     # W
    p_avail_motors = n * p_motor * 1000.0
    p_avail_batt = e_kwh * 1000.0 * c_rate
    p_avail = min(p_avail_motors, p_avail_batt)
    t_max = thrust_gain * (p_avail * fm * ETA_MOTOR * math.sqrt(2 * RHO * area)) ** (2.0 / 3.0)
    tw = t_max / weight
    endurance_min = e_kwh * 1000.0 * USABLE_BATTERY / p_hover * 60.0

    # ---- mission model (forward flight, headwind, rain) ----
    distance_km = float(brief.get("distance_km") or 0)
    wind = float(brief.get("wind_m_s") or 0)
    mission = None
    if distance_km > 0:
        v_air = float(design.get("cruise_speed_m_s", 15) or 15)
        v_air = max(5.0, min(60.0, v_air))
        cda = 0.012 * mass ** (2.0 / 3.0) * (1.15 if rain else 1.0)   # frontal drag area
        if ducted:
            cda *= 1.10
        p_ind_ideal = (weight / thrust_gain) ** 1.5 / math.sqrt(2 * RHO * area)
        p_ind = min(p_ind_ideal, weight ** 2 / (2 * RHO * area * v_air)) / fm / ETA_MOTOR
        p_par = 0.5 * RHO * v_air ** 3 * cda / ETA_MOTOR / 0.85      # prop propulsive eff
        p_cruise = p_ind + p_par
        headwind = 0.7 * wind                                       # route-averaged headwind component
        v_ground = v_air - headwind
        if v_ground > 0.5:
            t_s = distance_km * 1000.0 / v_ground
            e_need = (p_cruise * t_s + p_hover * 120.0) * 1.15      # +2 min hover, 15% reserve
        else:
            t_s, e_need = float("inf"), float("inf")
        e_have = e_kwh * 3.6e6 * USABLE_BATTERY
        mission = {"airspeed_m_s": round(v_air, 1), "ground_speed_m_s": round(v_ground, 1),
                   "cruise_power_kw": round(p_cruise / 1000, 2),
                   "flight_time_min": round(t_s / 60, 1) if t_s != float("inf") else None,
                   "energy_needed_kwh": round(e_need / 3.6e6, 2) if e_need != float("inf") else None,
                   "energy_usable_kwh": round(e_have / 3.6e6, 2),
                   "wind_m_s": wind, "headwind_m_s": round(headwind, 1), "rain": rain, "distance_km": distance_km}
    disk_loading = weight / area                           # N/m^2
    downwash_ms = math.sqrt(disk_loading / (2 * RHO))      # induced velocity

    report = {
        "planet": planet, "gravity": G, "air_density": RHO,
        "weight_n": round(weight, 1),
        "mass_kg": round(mass, 2),
        "mass_breakdown_kg": {
            "payload": round(payload, 2), "battery": round(m_battery, 2),
            "motors": round(m_motors, 2), "rotors": round(m_rotors, 2),
            "ducts": round(m_ducts, 2), "frame": round(m_frame, 2),
            "waterproofing": round(m_seal, 2),
        },
        "span_m": round(span, 2),
        "hover_power_kw": round(p_hover / 1000, 2),
        "available_power_kw": round(p_avail / 1000, 2),
        "battery_power_limit_kw": round(p_avail_batt / 1000, 2),
        "max_thrust_kn": round(t_max / 1000, 3),
        "thrust_to_weight": round(tw, 2),
        "endurance_min": round(endurance_min, 1),
        "disk_loading_n_m2": round(disk_loading, 0),
        "downwash_m_s": round(downwash_ms, 1),
    }
    if mission:
        report["mission"] = mission

    checks = []
    def chk(name, ok, detail):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    tw_need = 1.5 + wind / 40.0
    chk("lift_margin", tw >= tw_need,
        f"thrust/weight {tw:.2f} (need >= {tw_need:.2f} to climb and stay controllable"
        + (f" in {wind:.0f} m/s gusts)" if wind else ")"))
    if brief.get("endurance_min"):
        chk("endurance", endurance_min >= brief["endurance_min"],
            f"hovers {endurance_min:.1f} min (need >= {brief['endurance_min']} min)")
    if mission:
        chk("headway", mission["ground_speed_m_s"] >= 3.0,
            f"ground speed {mission['ground_speed_m_s']} m/s into a {mission['headwind_m_s']} m/s average headwind "
            f"(airspeed {mission['airspeed_m_s']} m/s; need >= 3 m/s of progress)")
        if mission["energy_needed_kwh"] is not None:
            chk("mission_energy", mission["energy_usable_kwh"] >= mission["energy_needed_kwh"],
                f"trip needs {mission['energy_needed_kwh']} kWh incl. reserve, battery gives "
                f"{mission['energy_usable_kwh']} kWh ({mission['flight_time_min']} min at "
                f"{mission['cruise_power_kw']} kW)")
        else:
            chk("mission_energy", False, "cannot complete the trip: no forward progress")
    chk("battery_power", p_avail_batt >= 1.5 * p_hover,
        f"battery can deliver {p_avail_batt/1000:.1f} kW at {c_rate}C "
        f"(need >= {1.5*p_hover/1000:.1f} kW = 1.5x hover)")
    p_ref = max(p_hover, mission["cruise_power_kw"] * 1000 if mission else 0)
    chk("motor_headroom", p_avail_motors >= 1.5 * p_ref,
        f"motors total {p_avail_motors/1000:.1f} kW (need >= {1.5*p_ref/1000:.1f} kW = 1.5x "
        f"{'cruise' if mission and p_ref > p_hover else 'hover'})")
    chk("size_limit", span <= brief["max_span_m"],
        f"span {span:.2f} m (limit {brief['max_span_m']} m)")
    max_dw = brief.get("max_downwash_m_s")
    if max_dw:
        chk("downwash_safety", downwash_ms <= max_dw,
            f"downwash {downwash_ms:.1f} m/s (limit {max_dw} m/s near people)")
    max_mass = brief.get("max_mass_kg")
    if max_mass:
        chk("mass_limit", mass <= max_mass,
            f"total mass {mass:.1f} kg (limit {max_mass} kg)")

    max_thrust = brief.get("max_thrust_kn")
    if max_thrust:
        chk("thrust_cap", t_max / 1000 <= max_thrust,
            f"max thrust {t_max/1000:.2f} kN (cap {max_thrust} kN)")
    max_power = brief.get("max_motor_power_kw")
    if max_power:
        chk("power_cap", p_avail_motors / 1000 <= max_power,
            f"installed motor power {p_avail_motors/1000:.1f} kW (cap {max_power} kW)")

    return {"ok": all(c["ok"] for c in checks), "report": report, "checks": checks}


def safe_simulate(design, brief):
    try:
        return simulate(design, brief)
    except DesignError as e:
        return {"ok": False, "report": {}, "checks": [
            {"name": "valid_design", "ok": False, "detail": str(e)}]}
