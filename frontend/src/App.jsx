import React, { useEffect, useMemo, useRef, useState } from "react";

const CANVAS_W = 980;
const CANVAS_H = 620;
const ROAD_W = 190;
const LANE_W = 42;
const INTERSECTION_SIZE = 160;
const SIGNAL_SWITCH_DELAY_MS = 90;

const WS_URL =
  import.meta.env.VITE_WS_URL ||
  `${window.location.protocol === "https:" ? "wss" : "ws"}://127.0.0.1:8000/ws`;

const MAX_TRACKED_VEHICLES = 40;
const MAX_RENDERED_VEHICLES = 30;

const SIGNAL = {
  NS_GREEN: "NS_GREEN",
  EW_GREEN: "EW_GREEN",
};

const DIRECTION_COLORS = {
  N: "#57cc99",
  S: "#5fa8ff",
  E: "#ffd166",
  W: "#ff8fa3",
};

const CAR_PALETTE = [
  "#54d7ff",
  "#ff8fab",
  "#ffd166",
  "#95d47a",
  "#c9a7ff",
  "#ffa94d",
  "#86efac",
  "#fda4af",
  "#a5b4fc",
  "#67e8f9",
  "#f9a8d4",
  "#fcd34d",
];

function hashString(value) {
  let hash = 0;
  for (let i = 0; i < value.length; i += 1) {
    hash = (hash * 31 + value.charCodeAt(i)) >>> 0;
  }
  return hash;
}

function pickVehicleColor(id, isInjected, direction) {
  if (isInjected) return "#f8fafc";
  const seed = hashString(`${id}:${direction}`);
  return (
    CAR_PALETTE[seed % CAR_PALETTE.length] ||
    DIRECTION_COLORS[direction] ||
    "#a8dadc"
  );
}

function lerp(a, b, t) {
  return a + (b - a) * t;
}

function buildRoadGeometry() {
  const centerX = CANVAS_W / 2;
  const centerY = CANVAS_H / 2;
  const halfInt = INTERSECTION_SIZE / 2;

  return {
    centerX,
    centerY,
    halfInt,
    stopLines: {
      n: centerY - halfInt - 14,
      s: centerY + halfInt + 14,
      e: centerX + halfInt + 14,
      w: centerX - halfInt - 14,
    },
    lanes: {
      nToS: centerX - LANE_W / 2,
      sToN: centerX + LANE_W / 2,
      eToW: centerY - LANE_W / 2,
      wToE: centerY + LANE_W / 2,
    },
  };
}

function targetPhaseToSignal(phase) {
  return phase === "NS" ? SIGNAL.NS_GREEN : SIGNAL.EW_GREEN;
}

function approachingStopLine(vehicle, geometry) {
  if (vehicle.direction === "N") {
    return (
      vehicle.y + vehicle.length >= geometry.stopLines.n &&
      vehicle.y < geometry.centerY
    );
  }
  if (vehicle.direction === "S") {
    return (
      vehicle.y - vehicle.length <= geometry.stopLines.s &&
      vehicle.y > geometry.centerY
    );
  }
  if (vehicle.direction === "E") {
    return (
      vehicle.x - vehicle.length <= geometry.stopLines.e &&
      vehicle.x > geometry.centerX
    );
  }
  return (
    vehicle.x + vehicle.length >= geometry.stopLines.w &&
    vehicle.x < geometry.centerX
  );
}

function isRedForVehicle(vehicle, signal) {
  const corridor =
    vehicle.direction === "N" || vehicle.direction === "S" ? "NS" : "EW";
  return (
    (corridor === "NS" && signal !== SIGNAL.NS_GREEN) ||
    (corridor === "EW" && signal !== SIGNAL.EW_GREEN)
  );
}

function clampToStopLine(vehicle, geometry) {
  if (vehicle.direction === "N")
    vehicle.y = geometry.stopLines.n - vehicle.length;
  if (vehicle.direction === "S")
    vehicle.y = geometry.stopLines.s + vehicle.length;
  if (vehicle.direction === "E")
    vehicle.x = geometry.stopLines.e + vehicle.length;
  if (vehicle.direction === "W")
    vehicle.x = geometry.stopLines.w - vehicle.length;
}

function getSpawnPoint(direction, g) {
  if (direction === "N") return { x: g.lanes.nToS, y: -40 };
  if (direction === "S") return { x: g.lanes.sToN, y: CANVAS_H + 40 };
  if (direction === "E") return { x: CANVAS_W + 40, y: g.lanes.eToW };
  return { x: -40, y: g.lanes.wToE };
}

export default function App() {
  const canvasRef = useRef(null);
  const rafRef = useRef(0);
  const wsRef = useRef(null);
  const reconnectTimerRef = useRef(0);

  const vehiclesRef = useRef(new Map());
  const signalRef = useRef(SIGNAL.NS_GREEN);
  const lastAppliedSwitchRef = useRef(-1);

  const statsRef = useRef({
    phase: "NS",
    timeSinceSwitch: 0,
    currentStep: 0,
    backendVehicles: 0,
    renderedVehicles: 0,
    online: false,
  });

  const uiTimerRef = useRef(0);

  const [injectCount, setInjectCount] = useState(1);
  const [uiStats, setUiStats] = useState({
    phase: "NS",
    timeSinceSwitch: 0,
    currentStep: 0,
    backendVehicles: 0,
    renderedVehicles: 0,
    online: false,
  });

  const geometry = useMemo(() => buildRoadGeometry(), []);

  useEffect(() => {
    const applySnapshot = (snapshot) => {
      if (!snapshot || !Array.isArray(snapshot.vehicles)) return;

      statsRef.current.phase = snapshot.phase || "NS";
      statsRef.current.currentStep = snapshot.current_step || 0;
      statsRef.current.timeSinceSwitch =
        (snapshot.current_step || 0) - (snapshot.last_switch_step || 0);
      statsRef.current.backendVehicles = snapshot.vehicles.length;
      statsRef.current.online = true;

      if (lastAppliedSwitchRef.current !== snapshot.last_switch_step) {
        lastAppliedSwitchRef.current = snapshot.last_switch_step;
        const targetSignal = targetPhaseToSignal(snapshot.phase);
        setTimeout(() => {
          signalRef.current = targetSignal;
        }, SIGNAL_SWITCH_DELAY_MS);
      }

      const seen = new Set();
      const now = performance.now();

      for (const incoming of snapshot.vehicles.slice(0, MAX_TRACKED_VEHICLES)) {
        if (!incoming?.id) continue;
        seen.add(incoming.id);

        const current = vehiclesRef.current.get(incoming.id);
        if (!current) {
          const isInjected = incoming.id.startsWith("car_user_");
          vehiclesRef.current.set(incoming.id, {
            id: incoming.id,
            x: incoming.x,
            y: incoming.y,
            targetX: incoming.x,
            targetY: incoming.y,
            speed: incoming.speed,
            targetSpeed: incoming.speed,
            direction: incoming.direction || "N",
            length: 17,
            width: 10,
            localState: "moving",
            lastSeen: now,
            ghost: false,
            isInjected,
            color: pickVehicleColor(
              incoming.id,
              isInjected,
              incoming.direction || "N",
            ),
          });
          continue;
        }

        current.targetX = incoming.x;
        current.targetY = incoming.y;
        current.targetSpeed = incoming.speed;
        current.direction = incoming.direction || current.direction;
        current.lastSeen = now;
        current.ghost = false;
        current.isInjected = incoming.id.startsWith("car_user_");
        if (!current.color) {
          current.color = pickVehicleColor(
            incoming.id,
            current.isInjected,
            current.direction,
          );
        }
      }

      for (const [id, vehicle] of vehiclesRef.current.entries()) {
        if (seen.has(id) || vehicle.ghost) continue;
        if (!vehicle.missingSince) vehicle.missingSince = now;
        if (now - vehicle.missingSince > 600) {
          vehiclesRef.current.delete(id);
        }
      }
    };

    const connect = () => {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        statsRef.current.online = true;
      };

      ws.onmessage = (event) => {
        try {
          const snapshot = JSON.parse(event.data);
          applySnapshot(snapshot);
        } catch {
          // Ignore malformed payloads.
        }
      };

      ws.onerror = () => {
        statsRef.current.online = false;
      };

      ws.onclose = () => {
        statsRef.current.online = false;
        reconnectTimerRef.current = window.setTimeout(connect, 1100);
      };
    };

    connect();

    return () => {
      window.clearTimeout(reconnectTimerRef.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let previousTimestamp = performance.now();

    const updateVehicles = () => {
      const now = performance.now();
      for (const [id, vehicle] of vehiclesRef.current.entries()) {
        vehicle.x = lerp(vehicle.x, vehicle.targetX, 0.2);
        vehicle.y = lerp(vehicle.y, vehicle.targetY, 0.2);
        vehicle.speed = lerp(vehicle.speed, vehicle.targetSpeed, 0.1);

        const red = isRedForVehicle(vehicle, signalRef.current);
        if (red && approachingStopLine(vehicle, geometry)) {
          vehicle.localState = "stopped";
          clampToStopLine(vehicle, geometry);
        } else {
          vehicle.localState = "moving";
        }

        if (vehicle.ghost && now - vehicle.lastSeen > 1200) {
          vehiclesRef.current.delete(id);
        }
      }
    };

    const loop = (now) => {
      const _dt = Math.min((now - previousTimestamp) / 1000, 0.05);
      previousTimestamp = now;

      updateVehicles();
      drawScene(ctx, geometry, vehiclesRef.current, signalRef.current);

      rafRef.current = requestAnimationFrame(loop);
    };

    rafRef.current = requestAnimationFrame(loop);
    uiTimerRef.current = window.setInterval(() => {
      const rendered = Math.min(
        vehiclesRef.current.size,
        MAX_RENDERED_VEHICLES,
      );
      statsRef.current.renderedVehicles = rendered;
      setUiStats({ ...statsRef.current });
    }, 260);

    return () => {
      cancelAnimationFrame(rafRef.current);
      window.clearInterval(uiTimerRef.current);
    };
  }, [geometry]);

  const addLocalGhosts = (direction, count) => {
    const now = performance.now();
    for (let i = 0; i < count; i += 1) {
      const id = `ghost_${direction}_${now}_${i}`;
      const pos = getSpawnPoint(direction, geometry);
      const offset = i * 18;
      if (direction === "N") pos.y -= offset;
      if (direction === "S") pos.y += offset;
      if (direction === "E") pos.x += offset;
      if (direction === "W") pos.x -= offset;

      vehiclesRef.current.set(id, {
        id,
        x: pos.x,
        y: pos.y,
        targetX: pos.x,
        targetY: pos.y,
        speed: 18,
        targetSpeed: 18,
        direction,
        length: 17,
        width: 10,
        localState: "moving",
        lastSeen: now,
        ghost: true,
        isInjected: true,
        color: "#f8fafc",
      });
    }
  };

  const injectTraffic = async (direction) => {
    addLocalGhosts(direction, injectCount);

    try {
      await fetch("/inject", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ direction, count: injectCount }),
      });
    } catch {}
  };

  return (
    <div className="app-shell">
      <header className="top-bar">
        <h1>Smart Traffic Control System</h1>
        <div className={`status-pill ${uiStats.online ? "online" : "offline"}`}>
          {uiStats.online ? "WEBSOCKET LIVE" : "WEBSOCKET RECONNECTING"}
        </div>
      </header>

      <main className="layout">
        <section className="panel controls">
          <h2>User Traffic Control</h2>
          <label htmlFor="injectCount">Cars per action: {injectCount}</label>
          <input
            id="injectCount"
            type="range"
            min="1"
            max="8"
            value={injectCount}
            onChange={(e) => setInjectCount(Number(e.target.value))}
          />
          <div className="btn-grid">
            <button onClick={() => injectTraffic("N")}>Inject North</button>
            <button onClick={() => injectTraffic("S")}>Inject South</button>
            <button onClick={() => injectTraffic("W")}>Inject East</button>
            <button onClick={() => injectTraffic("E")}>Inject West</button>
          </div>
        </section>

        <section className="canvas-wrap panel">
          <canvas ref={canvasRef} width={CANVAS_W} height={CANVAS_H} />
        </section>

        <section className="panel stats">
          <h2>Live State</h2>
          <p>
            Phase: <strong>{uiStats.phase}</strong>
          </p>
          <p>
            Step: <strong>{uiStats.currentStep}</strong>
          </p>
          <p>
            Time Since Switch: <strong>{uiStats.timeSinceSwitch}</strong>
          </p>
          <p>
            Rendered Vehicles: <strong>{uiStats.renderedVehicles}</strong>
          </p>
          <div className="phase-indicator">
            <span className={uiStats.phase === "NS" ? "active" : "idle"}>
              N/S ACTIVE
            </span>
            <span className={uiStats.phase === "EW" ? "active" : "idle"}>
              E/W ACTIVE
            </span>
          </div>
        </section>
      </main>
    </div>
  );
}

function drawScene(ctx, geometry, vehiclesMap, signal) {
  drawBackground(ctx);
  drawRoad(ctx, geometry);
  drawSignals(ctx, geometry, signal);

  const vehicles = Array.from(vehiclesMap.values())
    .sort((a, b) => (a.isInjected === b.isInjected ? 0 : a.isInjected ? -1 : 1))
    .slice(0, MAX_RENDERED_VEHICLES);

  drawCars(ctx, vehicles);
}

function drawBackground(ctx) {
  const gradient = ctx.createLinearGradient(0, 0, CANVAS_W, CANVAS_H);
  gradient.addColorStop(0, "#0f172a");
  gradient.addColorStop(1, "#1f2937");
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, CANVAS_W, CANVAS_H);

  ctx.fillStyle = "rgba(255,255,255,0.05)";
  for (let i = 0; i < 70; i += 1) {
    ctx.beginPath();
    ctx.arc(
      (i * 83) % CANVAS_W,
      (i * 149) % CANVAS_H,
      1 + (i % 2),
      0,
      Math.PI * 2,
    );
    ctx.fill();
  }
}

function drawRoad(ctx, g) {
  ctx.fillStyle = "#2b3440";
  ctx.fillRect((CANVAS_W - ROAD_W) / 2, 0, ROAD_W, CANVAS_H);
  ctx.fillRect(0, (CANVAS_H - ROAD_W) / 2, CANVAS_W, ROAD_W);

  ctx.fillStyle = "#384353";
  ctx.fillRect(
    g.centerX - INTERSECTION_SIZE / 2,
    g.centerY - INTERSECTION_SIZE / 2,
    INTERSECTION_SIZE,
    INTERSECTION_SIZE,
  );

  ctx.setLineDash([12, 12]);
  ctx.strokeStyle = "rgba(219, 234, 254, 0.75)";
  ctx.lineWidth = 2;

  ctx.beginPath();
  ctx.moveTo(g.centerX - LANE_W / 2, 0);
  ctx.lineTo(g.centerX - LANE_W / 2, CANVAS_H);
  ctx.stroke();

  ctx.beginPath();
  ctx.moveTo(g.centerX + LANE_W / 2, 0);
  ctx.lineTo(g.centerX + LANE_W / 2, CANVAS_H);
  ctx.stroke();

  ctx.beginPath();
  ctx.moveTo(0, g.centerY - LANE_W / 2);
  ctx.lineTo(CANVAS_W, g.centerY - LANE_W / 2);
  ctx.stroke();

  ctx.beginPath();
  ctx.moveTo(0, g.centerY + LANE_W / 2);
  ctx.lineTo(CANVAS_W, g.centerY + LANE_W / 2);
  ctx.stroke();

  ctx.setLineDash([]);

  ctx.strokeStyle = "#ff5d73";
  ctx.lineWidth = 3;

  ctx.beginPath();
  ctx.moveTo((CANVAS_W - ROAD_W) / 2, g.stopLines.n);
  ctx.lineTo((CANVAS_W + ROAD_W) / 2, g.stopLines.n);
  ctx.stroke();

  ctx.beginPath();
  ctx.moveTo((CANVAS_W - ROAD_W) / 2, g.stopLines.s);
  ctx.lineTo((CANVAS_W + ROAD_W) / 2, g.stopLines.s);
  ctx.stroke();

  ctx.beginPath();
  ctx.moveTo(g.stopLines.w, (CANVAS_H - ROAD_W) / 2);
  ctx.lineTo(g.stopLines.w, (CANVAS_H + ROAD_W) / 2);
  ctx.stroke();

  ctx.beginPath();
  ctx.moveTo(g.stopLines.e, (CANVAS_H - ROAD_W) / 2);
  ctx.lineTo(g.stopLines.e, (CANVAS_H + ROAD_W) / 2);
  ctx.stroke();
}

function drawSignals(ctx, g, signal) {
  drawSignalHead(
    ctx,
    g.centerX - 65,
    g.centerY - 110,
    signal === SIGNAL.NS_GREEN,
  );
  drawSignalHead(
    ctx,
    g.centerX + 65,
    g.centerY + 110,
    signal === SIGNAL.NS_GREEN,
  );
  drawSignalHead(
    ctx,
    g.centerX + 110,
    g.centerY - 65,
    signal === SIGNAL.EW_GREEN,
  );
  drawSignalHead(
    ctx,
    g.centerX - 110,
    g.centerY + 65,
    signal === SIGNAL.EW_GREEN,
  );
}

function drawSignalHead(ctx, x, y, green) {
  ctx.fillStyle = "#111827";
  ctx.fillRect(x - 10, y - 26, 20, 52);

  ctx.beginPath();
  ctx.arc(x, y - 10, 6, 0, Math.PI * 2);
  ctx.fillStyle = green ? "#3f1f1f" : "#ff4f64";
  ctx.fill();

  ctx.beginPath();
  ctx.arc(x, y + 10, 6, 0, Math.PI * 2);
  ctx.fillStyle = green ? "#42ff7b" : "#1f3a2b";
  ctx.fill();
}

function drawRoundedRect(ctx, x, y, width, height, radius) {
  const r = Math.min(radius, width / 2, height / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + width - r, y);
  ctx.quadraticCurveTo(x + width, y, x + width, y + r);
  ctx.lineTo(x + width, y + height - r);
  ctx.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
  ctx.lineTo(x + r, y + height);
  ctx.quadraticCurveTo(x, y + height, x, y + height - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

function drawCars(ctx, vehicles) {
  for (const vehicle of vehicles) {
    ctx.save();
    ctx.translate(vehicle.x, vehicle.y);

    if (vehicle.direction === "N" || vehicle.direction === "S") {
      ctx.rotate(vehicle.direction === "N" ? Math.PI / 2 : -Math.PI / 2);
    } else {
      ctx.rotate(vehicle.direction === "W" ? 0 : Math.PI);
    }

    const color =
      vehicle.color ||
      pickVehicleColor(vehicle.id, vehicle.isInjected, vehicle.direction);

    const bodyX = -vehicle.length / 2;
    const bodyY = -vehicle.width / 2;
    const bodyW = vehicle.length;
    const bodyH = vehicle.width;

    ctx.shadowColor = "rgba(0,0,0,0.35)";
    ctx.shadowBlur = 8;
    ctx.shadowOffsetY = 2;
    ctx.fillStyle = color;
    drawRoundedRect(ctx, bodyX, bodyY, bodyW, bodyH, 3.2);
    ctx.fill();

    ctx.shadowBlur = 0;
    ctx.shadowOffsetY = 0;

    const gloss = ctx.createLinearGradient(bodyX, bodyY, bodyX, bodyY + bodyH);
    gloss.addColorStop(0, "rgba(255,255,255,0.38)");
    gloss.addColorStop(0.45, "rgba(255,255,255,0.10)");
    gloss.addColorStop(1, "rgba(0,0,0,0.06)");
    ctx.fillStyle = gloss;
    drawRoundedRect(
      ctx,
      bodyX + 0.6,
      bodyY + 0.6,
      bodyW - 1.2,
      bodyH * 0.52,
      2.4,
    );
    ctx.fill();

    ctx.strokeStyle = "rgba(15, 23, 42, 0.55)";
    ctx.lineWidth = 0.8;
    drawRoundedRect(ctx, bodyX, bodyY, bodyW, bodyH, 3.2);
    ctx.stroke();

    ctx.fillStyle = "rgba(17, 24, 39, 0.75)";
    drawRoundedRect(
      ctx,
      bodyX + 1.7,
      bodyY + 1.2,
      vehicle.length * 0.45,
      vehicle.width - 2.4,
      2,
    );
    ctx.fill();

    ctx.fillStyle = "rgba(255,255,255,0.58)";
    drawRoundedRect(
      ctx,
      bodyX + vehicle.length * 0.56,
      bodyY + 1.9,
      vehicle.length * 0.28,
      vehicle.width - 3.8,
      1.5,
    );
    ctx.fill();

    ctx.fillStyle = "rgba(15, 23, 42, 0.92)";
    ctx.fillRect(bodyX + 1.1, bodyY - 0.9, 2.6, 1.2);
    ctx.fillRect(bodyX + bodyW - 3.7, bodyY - 0.9, 2.6, 1.2);
    ctx.fillRect(bodyX + 1.1, bodyY + bodyH - 0.3, 2.6, 1.2);
    ctx.fillRect(bodyX + bodyW - 3.7, bodyY + bodyH - 0.3, 2.6, 1.2);

    ctx.fillStyle = "rgba(255, 244, 214, 0.95)";
    ctx.fillRect(bodyX + bodyW - 1.6, bodyY + 1.8, 1.2, 1.1);
    ctx.fillRect(bodyX + bodyW - 1.6, bodyY + bodyH - 2.9, 1.2, 1.1);

    if (vehicle.localState === "stopped") {
      ctx.fillStyle = "#ff4f64";
      ctx.fillRect(vehicle.length / 2 - 2.2, -2.4, 2.4, 4.8);
    }

    ctx.restore();
  }
}
