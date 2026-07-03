# Route Optimizer

Reorder a list of stops to minimize total travel distance on real roads, with a fixed start and end point.

Given coordinates for a **start**, several **stops**, and an **end**, the service finds the best order to visit every stop exactly once while keeping the start and end fixed.

## How it works

1. **OpenRouteService** builds a road distance matrix (meters) between all locations.
2. **Google OR-Tools** solves the routing problem and returns the optimized visit order.

Distances are based on actual road networks, not straight-line distance.

## Project layout

| File | Purpose |
|---|---|
| `main.py` | FastAPI HTTP service (Docker entrypoint) |
| `route_optimizer.py` | Core optimizer + CLI |
| `route.json` | Example CLI input with addresses |
| `tests/test_route_optimizer.py` | Core optimizer + CLI unit tests |
| `tests/test_main.py` | FastAPI endpoint unit tests |
| `tests/test_live_integration.py` | Live OpenRouteService integration tests |
| `pytest.ini` | Pytest config (unit tests run by default) |
| `Dockerfile` | Container image for the HTTP API |
| `.env` | OpenRouteService API key (not committed) |

## Requirements

- Python 3.10+
- A free [OpenRouteService API key](https://openrouteservice.org/dev/#/signup)

## Installation

```bash
git clone https://github.com/elkingarcia11/route-optimizer.git
cd route-optimizer

python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## API key

```bash
cp .env.example .env
```

Edit `.env`:

```
ORS_API_KEY=your_actual_api_key
```

The CLI loads `.env` automatically. For Docker, pass the key with `--env-file .env` or `-e ORS_API_KEY=...`.

---

## HTTP API (Docker)

Build and run on port 8000:

```bash
docker build -t route-optimizer .
docker run --rm -p 8000:8000 --name route-optimizer --env-file .env route-optimizer
```

On a shared Docker network, other services (e.g. a Go API) reach it at:

```
http://route-optimizer:8000/optimize
```

Set `ROUTE_OPTIMIZER_URL` in the caller if the host differs.

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Health check |
| `/optimize` | POST | Optimize route (Go-compatible) |
| `/docs` | GET | Swagger UI |

### Request — `POST /optimize`

Each location is **`[longitude, latitude]`**. First = start, last = end, middle = stops.

```json
{
  "locations": [
    [-73.8955, 40.8515],
    [-73.91335, 40.87995],
    [-73.90774, 40.88467],
    [-73.90478, 40.83256],
    [-73.8955, 40.8515]
  ]
}
```

### Response

```json
{
  "routeIndexes": [0, 3, 2, 1, 4],
  "totalDistanceMeters": 21220,
  "orderedLocations": [
    [-73.8955, 40.8515],
    [-73.90478, 40.83256],
    [-73.90774, 40.88467],
    [-73.91335, 40.87995],
    [-73.8955, 40.8515]
  ]
}
```

| Field | Description |
|---|---|
| `routeIndexes` | Indices into the input `locations` array, in visit order |
| `totalDistanceMeters` | Total road distance for the optimized route |
| `orderedLocations` | `[lon, lat]` pairs in optimized visit order |

### curl example

```bash
curl -X POST http://localhost:8000/optimize \
  -H "Content-Type: application/json" \
  -d '{"locations": [[-73.8955, 40.8515], [-73.91335, 40.87995], [-73.90774, 40.88467], [-73.8955, 40.8515]]}'
```

### Go client

```go
POST http://route-optimizer:8000/optimize
Content-Type: application/json

{"locations": [[lon, lat], ...]}
```

Optional container env vars:

| Variable | Default | Description |
|---|---|---|
| `ORS_API_KEY` | — | OpenRouteService API key (required) |
| `ROUTE_OPTIMIZER_PROFILE` | `driving-car` | ORS travel profile |
| `ROUTE_OPTIMIZER_TIME_LIMIT` | `5` | OR-Tools solver time limit (seconds) |

Run locally without Docker:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

---

## CLI usage

Coordinates use **`latitude,longitude`** format.

### Command line

```bash
python route_optimizer.py \
  --start "40.7128,-74.0060" \
  --stop "40.7580,-73.9855" \
  --stop "40.7484,-73.9857" \
  --end "40.7308,-73.9973"
```

### JSON input file

Create `route.json`. Each location can include address fields for labeling (only `lat`/`lng` are used for routing):

```json
{
  "start": {
    "lat": 40.8515,
    "lng": -73.8955,
    "street_address_1": "2249 Washington Ave",
    "city": "Bronx",
    "state": "NY",
    "zip": ""
  },
  "stops": [
    {
      "lat": 40.87995,
      "lng": -73.91335,
      "street_address_1": "1 Adrian Ave",
      "city": "Bronx",
      "state": "NY",
      "zip": "10463"
    }
  ],
  "end": {
    "lat": 40.8515,
    "lng": -73.8955,
    "street_address_1": "2249 Washington Ave",
    "city": "Bronx",
    "state": "NY",
    "zip": ""
  },
  "profile": "driving-car",
  "time_limit_seconds": 5
}
```

Legacy `[lat, lng]` arrays are still supported for start, end, and stops.

```bash
python route_optimizer.py --input route.json
```

### Python module

```python
from route_optimizer import Location, optimize_route

result = optimize_route(
    start=Location(40.8515, -73.8955, street_address_1="2249 Washington Ave", city="Bronx", state="NY"),
    stops=[Location(40.87995, -73.91335, street_address_1="1 Adrian Ave", city="Bronx", state="NY", zip="10463")],
    end=Location(40.8515, -73.8955, street_address_1="2249 Washington Ave", city="Bronx", state="NY"),
    api_key="your_api_key",
)

print(result["ordered_locations"])
print(result["total_distance_meters"])
```

### CLI output

```json
{
  "ordered_locations": [
    {
      "lat": 40.8515,
      "lng": -73.8955,
      "street_address_1": "2249 Washington Ave",
      "city": "Bronx",
      "state": "NY",
      "zip": "",
      "label": "2249 Washington Ave, Bronx, NY"
    }
  ],
  "ordered_coordinates": [[40.8515, -73.8955], ...],
  "ordered_indices": [0, 2, 1, 3],
  "stop_order": [1, 0],
  "total_distance_meters": 21220,
  "distance_source": "openrouteservice",
  "profile": "driving-car"
}
```

### CLI options

| Flag | Default | Description |
|---|---|---|
| `--start` | — | Start coordinate as `lat,lng` |
| `--stop` | — | Stop coordinate (repeat for each stop) |
| `--end` | — | End coordinate as `lat,lng` |
| `--input`, `-i` | — | JSON input file |
| `--api-key` | from `.env` | OpenRouteService API key |
| `--profile` | `driving-car` | Travel mode |
| `--time-limit` | `5` | OR-Tools solver time limit (seconds) |

### Travel profiles

- `driving-car` (default)
- `driving-hgv`
- `foot-walking`
- `cycling-regular`

See the [OpenRouteService docs](https://openrouteservice.org/dev/#/api-docs) for the full list.

## Tests

```bash
source .venv/bin/activate      # if using a venv
pip install -r requirements.txt
pytest                         # unit tests only (mocked, no API calls)
pytest -m integration -v       # live OpenRouteService tests (uses .env)
pytest -v                      # all tests
```

By default, `pytest.ini` excludes integration tests (`-m "not integration"`).

| Test file | Coverage |
|---|---|
| `tests/test_route_optimizer.py` | `Location`, distance matrix, route optimization, CLI helpers, and `main` |
| `tests/test_main.py` | `/health`, `/optimize`, API key handling, request validation |
| `tests/test_live_integration.py` | End-to-end calls to OpenRouteService (skipped without `ORS_API_KEY`) |

Unit tests mock external services and do not require an API key. Integration tests require `ORS_API_KEY` in `.env` and make real API calls to OpenRouteService.
