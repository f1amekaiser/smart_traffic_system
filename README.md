# Smart Traffic Signal System

An intelligent traffic signal control system that uses a machine learning model to dynamically optimize signal phases based on real-time traffic conditions simulated in SUMO, with a React + Canvas frontend synchronized through WebSocket events.

## Overview

This project integrates:

- SUMO for traffic simulation
- A Random Forest model for signal decision-making
- LLM-based traffic scenario generation
- Flask API for control and WebSocket synchronization (`/inject`, `/ws`)
- React + HTML5 Canvas visualization frontend
- Logging for system behavior analysis

The system continuously monitors traffic metrics such as queue length, waiting time, and vehicle count, and adjusts signal timing accordingly.

## Project Structure

```text
SMART_TRAFFIC_SYSTEM/
|
|-- data/
|   |-- dataset_gen.py        # Generates dataset for training
|   `-- traffic_data.csv      # Training dataset
|
|-- model/
|   |-- train_model.py        # Trains Random Forest model
|   `-- traffic_rf_model.pkl  # Trained model
|
|-- reports/
|   `-- report.txt            # Logged signal decisions (not committed)
|
|-- first.net.xml             # SUMO network file
|-- first.rou.xml             # Route definitions
|-- first.sumocfg             # SUMO configuration
|
|-- frontend/                 # React + Canvas event-driven frontend
|   |-- src/
|   |-- package.json
|   `-- vite.config.js
|
|-- llm_gen.py                # LLM-based traffic scenario generator
|-- main.py                   # SUMO loop + Flask API server
|-- requirements.txt
`-- .env.example
```

## Requirements

### Python Dependencies

Install all required packages:

```bash
pip install -r requirements.txt
```

### Frontend Dependencies

```bash
cd frontend
npm install
```

### SUMO Installation

Download SUMO from:

https://www.eclipse.org/sumo/

After installation, add the following to your system PATH:

```text
C:\Program Files (x86)\Eclipse\Sumo\bin
C:\Program Files (x86)\Eclipse\Sumo\tools
```

Verify installation:

```bash
sumo-gui
```

## Environment Setup

Create a `.env` file in the root directory:

```env
OPENAI_API_KEY=your_api_key_here
```

## Running the Project

### Step 1: Train the Model (if needed)

```bash
python model/train_model.py
```

### Step 2: Run SUMO + Backend API

```bash
python main.py
```

The backend server starts on `http://127.0.0.1:8000`.

### Step 3: Run Frontend

In a second terminal:

```bash
cd frontend
npm run dev
```

Open the printed Vite URL (typically `http://127.0.0.1:5173`).

## Backend Data Contract

WebSocket stream (`/ws`) emits every ~250ms:

```json
{
  "vehicles": [
    {
      "id": "car_120_8372",
      "x": 442.8,
      "y": 211.5,
      "speed": 4.25,
      "direction": "N"
    }
  ],
  "phase": "NS",
  "current_step": 64,
  "last_switch_step": 42
}
```

Vehicle list is capped to a small subset for performance.

`POST /inject` accepts:

```json
{
  "direction": "N",
  "count": 3
}
```

Direction must be one of `N`, `S`, `E`, `W`.

## Execution Flow

1. Traffic scenario is generated using the LLM.
2. Vehicles are spawned in SUMO based on scenario parameters and user injections.
3. Traffic data is collected at each simulation step:
   - Queue length
   - Waiting time
   - Vehicle count
   - Average speed
4. Features are passed to the trained Random Forest model.
5. Model predicts the optimal signal phase.
6. Backend updates the phase deterministically and records `last_switch_step`.
7. Backend emits compact vehicle + signal snapshots on `/ws` every ~250ms.
8. Frontend keeps a ref-based vehicle map and interpolates position/speed updates smoothly.
9. Frontend vehicles obey local stop-line rules (red stop, green resume), independent of network jitter.
10. Decisions are:

- Printed in the console
- Logged in `reports/report.txt`

## Sample Log Output

```text
STEP 45 | NS GREEN | NS_Q=12, EW_Q=5 | NS_W=34.2, EW_W=10.1
STEP 78 | EW GREEN | NS_Q=3, EW_Q=15 | NS_W=8.4, EW_W=40.7
```

## Notes

- Ensure SUMO is properly installed and accessible.
- The trained model must exist in the `model/` directory.
- Logs are cleared automatically at the start of each run.
- The system is designed for a four-way junction simulation.
- Frontend sync is event-based and timestamp-driven, not per-vehicle backend tracking.

## Future Improvements

- Multi-junction traffic control
- Real-world map integration using OpenStreetMap
- Performance comparison with baseline strategies
- Advanced traffic pattern modeling

## License

This project is intended for academic and educational use.
