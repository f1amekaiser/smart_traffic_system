import traci
import random
import csv

sumoCmd = ["sumo", "-c", "first.sumocfg"]
traci.start(sumoCmd)

routes = ["r_0", "r_1", "r_2", "r_3"]

ns_edges = ["E0", "-E1", "E1", "-E0"]

data = []

step = 0
spawn_rate = 0.5
last_switch = 0

while step < 5000:
    traci.simulationStep()

    if random.random() < spawn_rate:
        route_id = random.choice(routes)
        veh_id = f"car_{step}"
        try:
            traci.vehicle.add(veh_id, route_id)
        except:
            pass

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

        if any(lane.startswith(e) for e in ns_edges):
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

    time_since_switch = step - last_switch

    ns_score = ns_queue * 2 + ns_wait * 0.5
    ew_score = ew_queue * 2 + ew_wait * 0.5

    if ns_score > ew_score:
        decision = 0
        last_switch = step
    elif ew_score > ns_score:
        decision = 1
    else:
        decision = random.choice([0, 1])

    delta_queue = ns_queue - ew_queue

    row = [
        ns_queue, ew_queue,
        ns_wait, ew_wait,
        ns_count, ew_count,
        ns_speed, ew_speed,
        time_since_switch,
        delta_queue,
        decision
    ]

    data.append(row)

    step += 1

traci.close()

with open("traffic_data.csv", "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow([
        "ns_queue", "ew_queue",
        "ns_wait", "ew_wait",
        "ns_count", "ew_count",
        "ns_speed", "ew_speed",
        "time_since_switch",
        "delta_queue",
        "decision"
    ])
    writer.writerows(data)