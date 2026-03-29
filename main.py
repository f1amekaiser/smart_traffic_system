import traci
import random
import joblib
from llm_gen import LLMScenarioGenerator
from dotenv import load_dotenv
import os
import time

load_dotenv()
key = os.getenv("OPENAI_API_KEY")

sumoCmd = [
    "sumo-gui",
    "-c", "first.sumocfg",
    "--delay", "120",
    "--step-length", "0.4"
]
traci.start(sumoCmd)

model = joblib.load("model/traffic_rf_model.pkl")

REPORT_DIR = "reports"
REPORT_FILE = os.path.join(REPORT_DIR, "report.txt")

os.makedirs(REPORT_DIR, exist_ok=True)

with open(REPORT_FILE, "w") as f:
    f.write("=== TRAFFIC SIGNAL REPORT ===\n\n")

routes = {
    "N": "r_0",
    "S": "r_1",
    "E": "r_2",
    "W": "r_3"
}

tls_id = traci.trafficlight.getIDList()[0]

gen = LLMScenarioGenerator(api_key=key)
scenario = gen.generate_scenario()

base_spawn = scenario["spawn_rate"] * 0.3
heavy_dirs = scenario["heavy_directions"]

COLORS = [
    (255, 0, 0),
    (0, 255, 0),
    (0, 0, 255),
    (255, 255, 0),
    (255, 165, 0),
    (255, 192, 203),
    (128, 0, 128),
    (0, 255, 255)
]

step = 0
last_switch = 0
MIN_GREEN = 20
THRESHOLD = 2

def choose_direction():
    if random.random() < 0.6:
        return random.choice(heavy_dirs)
    return random.choice(["N", "S", "E", "W"])

def switch_phase_safe(current_phase, target_phase):
    if current_phase == target_phase:
        return

    if current_phase == 0 and target_phase == 2:
        traci.trafficlight.setPhase(tls_id, 1)
        for _ in range(3):
            traci.simulationStep()
        traci.trafficlight.setPhase(tls_id, 2)

    elif current_phase == 2 and target_phase == 0:
        traci.trafficlight.setPhase(tls_id, 3)
        for _ in range(3):
            traci.simulationStep()
        traci.trafficlight.setPhase(tls_id, 0)

while True:
    traci.simulationStep()
    time.sleep(0.03)

    lanes = traci.lane.getIDList()

    ns_queue = ew_queue = 0
    ns_wait = ew_wait = 0
    ns_count = ew_count = 0
    ns_speed = ew_speed = 0

    for lane in lanes:
        q = traci.lane.getLastStepHaltingNumber(lane)
        w = traci.lane.getWaitingTime(lane)
        c = traci.lane.getLastStepVehicleNumber(lane)
        s = traci.lane.getLastStepMeanSpeed(lane)

        if (
            lane.startswith("E0") or
            lane.startswith("-E1") or
            lane.startswith("E1") or
            lane.startswith("-E0")
        ):
            ns_queue += q
            ns_wait += w
            ns_count += c
            ns_speed += s
        else:
            ew_queue += q
            ew_wait += w
            ew_count += c
            ew_speed += s

    if ns_count > 0:
        ns_speed /= ns_count
    if ew_count > 0:
        ew_speed /= ew_count

    total_queue = ns_queue + ew_queue

    if total_queue > 30:
        spawn_rate = 0
    elif total_queue > 20:
        spawn_rate = 0.05
    elif total_queue > 12:
        spawn_rate = 0.1
    elif total_queue > 6:
        spawn_rate = 0.2
    else:
        spawn_rate = base_spawn

    if traci.vehicle.getIDCount() < 45 and random.random() < (spawn_rate * 0.9):
        direction = choose_direction()
        route_id = routes[direction]
        veh_id = f"car_{step}_{random.randint(1000,9999)}"

        try:
            traci.vehicle.add(veh_id, route_id)

            traci.vehicle.setColor(veh_id, random.choice(COLORS))

            traci.vehicle.setMaxSpeed(veh_id, 6)
            traci.vehicle.setSpeed(veh_id, 4)

        except:
            pass

    time_since_switch = step - last_switch
    delta_queue = ns_queue - ew_queue
    total_wait = ns_wait + ew_wait
    queue_ratio = (ns_queue + 1) / (ew_queue + 1)
    wait_ratio = (ns_wait + 1) / (ew_wait + 1)

    state = [[
        ns_queue, ew_queue,
        ns_wait, ew_wait,
        ns_count, ew_count,
        ns_speed, ew_speed,
        time_since_switch,
        delta_queue,
        total_queue,
        total_wait,
        queue_ratio,
        wait_ratio
    ]]

    decision = model.predict(state)[0]

    target_phase = 0 if decision == 0 else 2
    current_phase = traci.trafficlight.getPhase(tls_id)

    if step - last_switch > MIN_GREEN and abs(ns_queue - ew_queue) > THRESHOLD:
        if current_phase != target_phase:
            log_msg = (
                f"STEP {step} | "
                f"{'NS GREEN' if decision == 0 else 'EW GREEN'} | "
                f"NS_Q={ns_queue}, EW_Q={ew_queue} | "
                f"NS_W={round(ns_wait,1)}, EW_W={round(ew_wait,1)}"
            )

            print("\n " + log_msg + "\n")

            with open(REPORT_FILE, "a") as f:
                f.write(log_msg + "\n")
                switch_phase_safe(current_phase, target_phase)
                last_switch = step

    step += 1
