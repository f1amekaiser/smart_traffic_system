import os
import random
import threading
import time
from collections import defaultdict
import json

import joblib
import traci
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_sock import Sock

from llm_gen import LLMScenarioGenerator

load_dotenv()
key = os.getenv("OPENAI_API_KEY")

REPORT_DIR = "reports"
REPORT_FILE = os.path.join(REPORT_DIR, "report.txt")

os.makedirs(REPORT_DIR, exist_ok=True)

with open(REPORT_FILE, "w", encoding="utf-8") as f:
    f.write("=== TRAFFIC SIGNAL REPORT ===\n\n")


class SumoTrafficService:
    def __init__(self) -> None:
        self.model = joblib.load("model/traffic_rf_model.pkl")
        self.canvas_w = 980
        self.canvas_h = 620
        self.max_ws_vehicles = 35

        self.routes = {
            "N": "r_0",
            "S": "r_1",
            "E": "r_2",
            "W": "r_3",
        }
        self.route_to_direction = {
            "r_0": "N",
            "r_1": "S",
            "r_2": "E",
            "r_3": "W",
        }

        self.colors = [
            (255, 0, 0),
            (0, 255, 0),
            (0, 0, 255),
            (255, 255, 0),
            (255, 165, 0),
            (255, 192, 203),
            (128, 0, 128),
            (0, 255, 255),
        ]

        self.min_green = 20
        self.threshold = 2

        self.step = 0
        self.last_switch = 0
        self.tls_id = ""
        self.phase_label = "NS"
        self.net_min_x = 0.0
        self.net_min_y = 0.0
        self.net_max_x = 1.0
        self.net_max_y = 1.0

        self.state_lock = threading.Lock()
        self.inject_lock = threading.Lock()
        self.pending_injections = defaultdict(int)
        self.ws_lock = threading.Lock()
        self.ws_payload = {
            "vehicles": [],
            "phase": "NS",
            "current_step": 0,
            "last_switch_step": 0,
        }

        self.state = {
            "phase": "NS",
            "last_switch_step": 0,
            "current_step": 0,
            "ns_queue": 0,
            "ew_queue": 0,
            "ns_speed": 0.0,
            "ew_speed": 0.0,
        }

        gen = LLMScenarioGenerator(api_key=key)
        scenario = gen.generate_scenario()
        self.base_spawn = scenario["spawn_rate"] * 0.2
        self.heavy_dirs = scenario["heavy_directions"]

    @staticmethod
    def to_float(value) -> float:
        if isinstance(value, tuple):
            return float(value[0]) if value else 0.0
        return float(value)

    @staticmethod
    def to_int(value) -> int:
        if isinstance(value, tuple):
            return int(value[0]) if value else 0
        return int(value)

    def choose_direction(self) -> str:
        if random.random() < 0.55:
            return random.choice(self.heavy_dirs)
        return random.choice(["N", "S", "E", "W"])

    def get_direction_for_vehicle(self, vehicle_id: str) -> str:
        try:
            route_id = traci.vehicle.getRouteID(vehicle_id)
            if route_id in self.route_to_direction:
                return self.route_to_direction[route_id]
        except traci.TraCIException:
            pass
        return "N"

    def to_canvas_position(self, x: float, y: float) -> tuple[float, float]:
        width = max(1e-6, self.net_max_x - self.net_min_x)
        height = max(1e-6, self.net_max_y - self.net_min_y)

        norm_x = (x - self.net_min_x) / width
        norm_y = (y - self.net_min_y) / height

        canvas_x = max(0.0, min(float(self.canvas_w), norm_x * self.canvas_w))
        canvas_y = max(0.0, min(float(self.canvas_h), self.canvas_h - (norm_y * self.canvas_h)))
        return (canvas_x, canvas_y)

    def build_vehicle_snapshot(self) -> list[dict]:
        vehicle_ids = traci.vehicle.getIDList()
        vehicle_ids = sorted(vehicle_ids, key=lambda vid: (0 if vid.startswith("car_user_") else 1, vid))
        vehicle_ids = vehicle_ids[: self.max_ws_vehicles]

        snapshot = []
        for vehicle_id in vehicle_ids:
            try:
                px, py = traci.vehicle.getPosition(vehicle_id)
                speed = self.to_float(traci.vehicle.getSpeed(vehicle_id))
                cx, cy = self.to_canvas_position(px, py)
                snapshot.append(
                    {
                        "id": vehicle_id,
                        "x": round(cx, 2),
                        "y": round(cy, 2),
                        "speed": round(speed, 3),
                        "direction": self.get_direction_for_vehicle(vehicle_id),
                    }
                )
            except traci.TraCIException:
                continue
        return snapshot

    def set_ws_payload(self, payload: dict) -> None:
        with self.ws_lock:
            self.ws_payload = payload

    def get_ws_payload_json(self) -> str:
        with self.ws_lock:
            return json.dumps(self.ws_payload)

    def switch_phase_safe(self, current_phase: int, target_phase: int) -> None:
        if current_phase == target_phase:
            return

        if current_phase == 0 and target_phase == 2:
            traci.trafficlight.setPhase(self.tls_id, 1)
            for _ in range(3):
                traci.simulationStep()
            traci.trafficlight.setPhase(self.tls_id, 2)
            self.phase_label = "EW"
        elif current_phase == 2 and target_phase == 0:
            traci.trafficlight.setPhase(self.tls_id, 3)
            for _ in range(3):
                traci.simulationStep()
            traci.trafficlight.setPhase(self.tls_id, 0)
            self.phase_label = "NS"

    def lane_to_corridor(self, lane_id: str) -> str:
        if (
            lane_id.startswith("E0")
            or lane_id.startswith("-E1")
            or lane_id.startswith("E1")
            or lane_id.startswith("-E0")
        ):
            return "NS"
        return "EW"

    def drain_injections(self) -> None:
        with self.inject_lock:
            directions = dict(self.pending_injections)
            self.pending_injections.clear()

        for direction, count in directions.items():
            for _ in range(max(0, count)):
                veh_id = f"car_user_{self.step}_{random.randint(1000, 9999)}"
                route_id = self.routes.get(direction)
                if not route_id:
                    continue
                try:
                    traci.vehicle.add(veh_id, route_id)
                    traci.vehicle.setColor(veh_id, (245, 245, 245))
                    traci.vehicle.setMaxSpeed(veh_id, 6)
                    traci.vehicle.setSpeed(veh_id, 5)
                except traci.TraCIException:
                    continue

    def maybe_spawn_background_vehicle(self, total_queue: int) -> None:
        if total_queue > 25:
            spawn_rate = 0
        elif total_queue > 15:
            spawn_rate = 0.03
        elif total_queue > 10:
            spawn_rate = 0.08
        elif total_queue > 6:
            spawn_rate = 0.14
        else:
            spawn_rate = self.base_spawn

        if self.to_int(traci.vehicle.getIDCount()) < 40 and random.random() < (spawn_rate * 0.9):
            direction = self.choose_direction()
            route_id = self.routes[direction]
            veh_id = f"car_{self.step}_{random.randint(1000, 9999)}"
            try:
                traci.vehicle.add(veh_id, route_id)
                traci.vehicle.setColor(veh_id, random.choice(self.colors))
                traci.vehicle.setMaxSpeed(veh_id, 6)
                traci.vehicle.setSpeed(veh_id, 4)
            except traci.TraCIException:
                return

    def update_decision_and_maybe_switch(
        self,
        ns_queue: int,
        ew_queue: int,
        ns_wait: float,
        ew_wait: float,
        ns_count: int,
        ew_count: int,
        ns_speed: float,
        ew_speed: float,
    ) -> None:
        time_since_switch = self.step - self.last_switch
        delta_queue = ns_queue - ew_queue
        total_queue = ns_queue + ew_queue
        total_wait = ns_wait + ew_wait
        queue_ratio = (ns_queue + 1) / (ew_queue + 1)
        wait_ratio = (ns_wait + 1) / (ew_wait + 1)

        features = [[
            ns_queue,
            ew_queue,
            ns_wait,
            ew_wait,
            ns_count,
            ew_count,
            ns_speed,
            ew_speed,
            time_since_switch,
            delta_queue,
            total_queue,
            total_wait,
            queue_ratio,
            wait_ratio,
        ]]

        decision = self.model.predict(features)[0]
        target_phase = 0 if decision == 0 else 2
        current_phase = self.to_int(traci.trafficlight.getPhase(self.tls_id))

        if self.step - self.last_switch > self.min_green and abs(ns_queue - ew_queue) > self.threshold:
            if current_phase != target_phase:
                log_msg = (
                    f"STEP {self.step} | "
                    f"{'NS GREEN' if decision == 0 else 'EW GREEN'} | "
                    f"NS_Q={ns_queue}, EW_Q={ew_queue} | "
                    f"NS_W={round(ns_wait, 1)}, EW_W={round(ew_wait, 1)}"
                )
                print("\n " + log_msg + "\n")
                with open(REPORT_FILE, "a", encoding="utf-8") as f:
                    f.write(log_msg + "\n")
                self.switch_phase_safe(current_phase, target_phase)
                self.last_switch = self.step

    def run_loop(self) -> None:
        sumo_cmd = [
            "sumo-gui",
            "-c",
            "first.sumocfg",
            "--delay",
            "120",
            "--step-length",
            "0.4",
        ]
        traci.start(sumo_cmd)
        self.tls_id = traci.trafficlight.getIDList()[0]
        (self.net_min_x, self.net_min_y), (self.net_max_x, self.net_max_y) = traci.simulation.getNetBoundary()
        startup_phase = traci.trafficlight.getPhase(self.tls_id)
        self.phase_label = "NS" if startup_phase in (0, 1) else "EW"

        while True:
            traci.simulationStep()
            time.sleep(0.03)

            self.drain_injections()

            lanes = traci.lane.getIDList()

            ns_queue = ew_queue = 0
            ns_wait = ew_wait = 0.0
            ns_count = ew_count = 0
            ns_speed = ew_speed = 0.0

            for lane in lanes:
                q = self.to_int(traci.lane.getLastStepHaltingNumber(lane))
                w = self.to_float(traci.lane.getWaitingTime(lane))
                c = self.to_int(traci.lane.getLastStepVehicleNumber(lane))
                s = self.to_float(traci.lane.getLastStepMeanSpeed(lane))

                if self.lane_to_corridor(lane) == "NS":
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
            self.maybe_spawn_background_vehicle(total_queue)
            self.update_decision_and_maybe_switch(
                ns_queue,
                ew_queue,
                ns_wait,
                ew_wait,
                ns_count,
                ew_count,
                ns_speed,
                ew_speed,
            )

            with self.state_lock:
                self.state = {
                    "phase": self.phase_label,
                    "last_switch_step": self.last_switch,
                    "current_step": self.step,
                    "ns_queue": int(ns_queue),
                    "ew_queue": int(ew_queue),
                    "ns_speed": float(round(ns_speed, 3)),
                    "ew_speed": float(round(ew_speed, 3)),
                }

            self.set_ws_payload(
                {
                    "vehicles": self.build_vehicle_snapshot(),
                    "phase": self.phase_label,
                    "current_step": self.step,
                    "last_switch_step": self.last_switch,
                }
            )

            self.step += 1

    def get_state(self) -> dict:
        with self.state_lock:
            return dict(self.state)

    def queue_injection(self, direction: str, count: int) -> bool:
        direction = direction.upper()
        if direction not in self.routes:
            return False
        with self.inject_lock:
            self.pending_injections[direction] += max(1, int(count))
        return True


traffic_service = SumoTrafficService()
app = Flask(__name__)
sock = Sock(app)


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


@app.route("/state", methods=["GET"])
def get_state():
    return jsonify(traffic_service.get_state())


@app.route("/inject", methods=["POST", "OPTIONS"])
def inject_traffic():
    if request.method == "OPTIONS":
        return ("", 204)

    payload = request.get_json(silent=True) or {}
    direction = str(payload.get("direction", "")).upper()
    count = int(payload.get("count", 1))

    if not traffic_service.queue_injection(direction, count):
        return jsonify({"ok": False, "error": "direction must be one of N,S,E,W"}), 400

    return jsonify({"ok": True, "queued": count, "direction": direction})


@sock.route("/ws")
def ws_stream(ws):
    while True:
        try:
            ws.send(traffic_service.get_ws_payload_json())
            time.sleep(0.25)
        except Exception:
            break


if __name__ == "__main__":
    sim_thread = threading.Thread(target=traffic_service.run_loop, daemon=True)
    sim_thread.start()
    app.run(host="0.0.0.0", port=8000, debug=False)
