# Route Optimizer

Optimize a single route from a fixed start through all stops to a fixed end, using real road networks from OpenRouteService.

Given **start**, **end**, and at least **two stops** (HTTP API) or coordinates (CLI), the service finds the visit order that **minimizes total drive time** using Google OR-Tools.

## How it works

1. **OpenRouteService** builds road **distance** (meters) and **duration** (seconds) matrices between all locations.
2. **Google OR-Tools** minimizes total **drive time** (Guided Local Search, 5s default) and returns the optimized visit order.
3. The response reports both **total drive time** and **total road distance** for the resulting route.

Routes are based on actual road networks, not straight-line distance.

## Project layout

| File | Purpose |
|---|---|
| `main.py` | FastAPI HTTP service (Docker entrypoint) |
| `route_optimizer.py` | Core optimizer (`optimize_route`) used by the API |
| `examples/` | Sample HTTP request/response JSON payloads |
| `utils/` | Optional CLI, benchmarks, multi-route helpers, and sample data (not shipped in Docker) |
| `utils/samples/` | Example CLI input files (`route.json`, `routes.json`) |
| `tests/test_route_optimizer.py` | Core optimizer unit tests |
| `tests/test_main.py` | FastAPI endpoint unit tests |
| `tests/test_live_integration.py` | Live OpenRouteService integration tests |
| `pytest.ini` | Pytest config (unit tests run by default) |
| `Dockerfile` | Container image for the HTTP API |
| `.env` | OpenRouteService API key for CLI (not committed) |
| `.env.example` | Example env file for CLI setup |

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

### HTTP API

`apiKey` is **required in every** `POST /optimize` request body:

```json
{
  "apiKey": "your_openrouteservice_api_key",
  "start": { ... },
  "end": { ... },
  "stops": [ ... ]
}
```

The HTTP API does **not** read `ORS_API_KEY` from the environment.

### CLI and local development

```bash
cp .env.example .env
```

Edit `.env`:

```
ORS_API_KEY=your_actual_api_key
```

The CLI loads `.env` automatically and uses `ORS_API_KEY` when `--api-key` is not passed.

---

## HTTP API (Docker)

Build and run on port 8000:

```bash
docker build -t route-optimizer .
docker run --rm -p 8000:8000 --name route-optimizer route-optimizer
```

The image uses `requirements-docker.txt` (runtime deps only — no pytest/httpx).

On a shared Docker network, other services reach it at:

```
http://route-optimizer:8000/optimize
```

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Health check |
| `/optimize` | POST | Optimize one route (start → stops → end) |
| `/docs` | GET | Swagger UI (interactive API docs) |
| `/redoc` | GET | ReDoc API reference |
| `/openapi.json` | GET | OpenAPI 3 schema |

Sample request/response JSON files live in [`examples/`](examples/).

### Quick start with curl

Health check:

```bash
curl http://localhost:8000/health
```

Optimize route (see [`examples/optimize.request.json`](examples/optimize.request.json)):

```bash
curl -X POST http://localhost:8000/optimize \
  -H "Content-Type: application/json" \
  -d @examples/optimize.request.json
```

Replace `your_openrouteservice_api_key` in the example file with your real key before running.

Expected response shape: [`examples/optimize.response.json`](examples/optimize.response.json).

### Address schema

The HTTP API uses the same shape as Go `core.Address`:

| Field | Type | Description |
|---|---|---|
| `address1` | string | Primary street address |
| `address2` | string | Secondary street address |
| `apartment` | string | Apartment or unit |
| `city` | string | City |
| `country` | string | Country |
| `state` | string | State or province |
| `zipcode` | string | Postal code |
| `location` | object | GeoJSON point (`type`, `coordinates`) |
| `location.coordinates` | `[number, number]` | `[longitude, latitude]` |
| `verification` | object | Optional pass-through metadata (not used for routing) |
| `verification.is_verified` | boolean | Whether the address is verified upstream |
| `verification.verified_at` | string | Verification timestamp (ISO-8601) |

Response addresses include all input fields plus `routeOrder` (1-based visit position: start = 1, stops follow, end = last).

### Request — `POST /optimize`

| Field | Type | Required | Description |
|---|---|---|---|
| `apiKey` | string | yes | OpenRouteService API key |
| `start` | Address | yes | Route start |
| `end` | Address | yes | Route end |
| `stops` | Address[] | yes | At least **two** stop addresses |

```json
{
  "apiKey": "your_openrouteservice_api_key",
  "start": {
    "address1": "2249 Washington Ave",
    "city": "Bronx",
    "state": "NY",
    "zipcode": "10457",
    "location": {
      "type": "Point",
      "coordinates": [-73.89406, 40.854388]
    }
  },
  "end": {
    "address1": "2249 Washington Ave",
    "city": "Bronx",
    "state": "NY",
    "zipcode": "10457",
    "location": {
      "type": "Point",
      "coordinates": [-73.89406, 40.854388]
    }
  },
  "stops": [
    {
      "address1": "1 Adrian Ave",
      "city": "Bronx",
      "state": "NY",
      "location": {
        "type": "Point",
        "coordinates": [-73.91335, 40.87995]
      }
    },
    {
      "address1": "125 W 228th St",
      "city": "Bronx",
      "state": "NY",
      "location": {
        "type": "Point",
        "coordinates": [-73.90774, 40.88467]
      }
    }
  ]
}
```

`location.coordinates` is GeoJSON order: **`[longitude, latitude]`**.

Optional address fields: `address2`, `apartment`, `country`, and `verification` (`is_verified`, `verified_at`).

### Response

```json
{
  "addresses": [
    {
      "address1": "2249 Washington Ave",
      "city": "Bronx",
      "state": "NY",
      "location": {
        "type": "Point",
        "coordinates": [-73.89406, 40.854388]
      },
      "routeOrder": 1
    }
  ],
  "totalDistanceMeters": 7509,
  "totalDurationSeconds": 892
}
```

| Field | Description |
|---|---|
| `addresses` | Start, stops, and end in time-optimized visit order |
| `routeOrder` | 1-based position in the route (map to stop sequence, e.g. `pickup.routeNumber`) |
| `totalDurationSeconds` | Total drive time in seconds (optimization objective) |
| `totalDistanceMeters` | Total road distance in meters (reporting) |

### Go client

Map each stop's `routeOrder` to your domain model (e.g. `pickup.routeNumber`). Skip start/end if those are depot-only and not pickups.

```go
POST http://route-optimizer:8000/optimize
Content-Type: application/json

{
  "apiKey": "...",
  "start": {...core.Address...},
  "end": {...core.Address...},
  "stops": [{...core.Address...}, ...]
}
```

Optional container env vars:

| Variable | Default | Description |
|---|---|---|
| `ROUTE_OPTIMIZER_PROFILE` | `driving-car` | ORS travel profile |
| `ROUTE_OPTIMIZER_TIME_LIMIT` | `5` | OR-Tools solver time limit (seconds) |

Run locally without Docker:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Then open Swagger UI at [http://localhost:8000/docs](http://localhost:8000/docs) to try the API interactively.

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
    },
    {
      "lat": 40.88467,
      "lng": -73.90774,
      "street_address_1": "125 W 228th St",
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
python route_optimizer.py --input utils/samples/route.json
```

Or run the CLI module directly:

```bash
python -m utils.cli --input utils/samples/route.json
```

### Python module

```python
from route_optimizer import Location, optimize_route

result = optimize_route(
    start=Location(40.8515, -73.8955, street_address_1="2249 Washington Ave", city="Bronx", state="NY"),
    stops=[
        Location(40.87995, -73.91335, street_address_1="1 Adrian Ave", city="Bronx", state="NY", zip="10463"),
        Location(40.88467, -73.90774, street_address_1="125 W 228th St", city="Bronx", state="NY"),
    ],
    end=Location(40.8515, -73.8955, street_address_1="2249 Washington Ave", city="Bronx", state="NY"),
    api_key="your_api_key",
)

print(result["ordered_locations"])
print(result["total_duration_seconds"])
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
  "total_duration_seconds": 3840,
  "optimization_metric": "duration",
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
| `tests/test_route_optimizer.py` | `Location`, distance matrix, route optimization, CLI helpers |
| `tests/test_main.py` | `/health`, `/optimize` validation and responses |
| `tests/test_live_integration.py` | Live HTTP + optimizer calls (skipped without `ORS_API_KEY` in `.env`) |

Unit tests mock external services and do not require an API key. Integration tests read `ORS_API_KEY` from `.env` for live OpenRouteService calls and pass it as `apiKey` in HTTP request payloads.
