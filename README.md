# Route Optimizer

Reorder a list of stops to minimize total travel distance on real roads, with a fixed start and end point.

Given a list of **addresses** (HTTP API) or coordinates (CLI), the service finds the best order to visit every stop exactly once while keeping the start and end fixed.

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

### HTTP API

Pass `apiKey` in each `POST /optimize` request body. This lets callers rotate keys when one expires or hits its limit without redeploying:

```json
{
  "apiKey": "your_openrouteservice_api_key",
  "addresses": [...]
}
```

If `apiKey` is omitted, the service falls back to the `ORS_API_KEY` environment variable.

### CLI and local development

```bash
cp .env.example .env
```

Edit `.env`:

```
ORS_API_KEY=your_actual_api_key
```

The CLI loads `.env` automatically. For Docker without per-request keys, pass the env var with `--env-file .env` or `-e ORS_API_KEY=...`.

---

## HTTP API (Docker)

Build and run on port 8000:

```bash
docker build -t route-optimizer .
docker run --rm -p 8000:8000 --name route-optimizer --env-file .env route-optimizer
```

The image uses `requirements-docker.txt` (runtime deps only — no pytest/httpx).

On a shared Docker network, other services (e.g. a Go API) reach it at:

```
http://route-optimizer:8000/optimize
```

Set `ROUTE_OPTIMIZER_URL` in the caller if the host differs.

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Health check |
| `/optimize` | POST | Optimize route using `core.Address` payloads |
| `/docs` | GET | Swagger UI (interactive API docs) |
| `/redoc` | GET | ReDoc API reference |
| `/openapi.json` | GET | OpenAPI 3 schema |

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
| `verification` | object | Optional Google verification metadata |
| `verification.is_verified` | boolean | Whether the address is verified |
| `verification.verified_at` | string | Verification timestamp |

Response addresses include all input fields plus `routeOrder` (1-based visit position).

### Request — `POST /optimize`

| Field | Type | Description |
|---|---|---|
| `apiKey` | string | OpenRouteService API key (preferred; rotates per request) |
| `addresses` | array | Ordered list of addresses — first is start, last is end |

```json
{
  "apiKey": "your_openrouteservice_api_key",
  "addresses": [
    {
      "address1": "2249 Washington Ave",
      "city": "Bronx",
      "state": "NY",
      "zipcode": "10451",
      "location": {
        "type": "Point",
        "coordinates": [-73.8955, 40.8515]
      }
    },
    {
      "address1": "1 Adrian Ave",
      "city": "Bronx",
      "state": "NY",
      "zipcode": "10463",
      "location": {
        "type": "Point",
        "coordinates": [-73.91335, 40.87995]
      }
    },
    {
      "address1": "2249 Washington Ave",
      "city": "Bronx",
      "state": "NY",
      "location": {
        "type": "Point",
        "coordinates": [-73.8955, 40.8515]
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
      "address2": "",
      "apartment": "",
      "city": "Bronx",
      "country": "",
      "state": "NY",
      "zipcode": "10451",
      "location": {
        "type": "Point",
        "coordinates": [-73.8955, 40.8515]
      },
      "verification": null,
      "routeOrder": 1
    }
  ],
  "totalDistanceMeters": 21220
}
```

| Field | Description |
|---|---|
| `addresses` | Input addresses in optimized visit order |
| `routeOrder` | 1-based position in the optimized route |
| `totalDistanceMeters` | Total road distance for the optimized route |

### curl example

```bash
curl -X POST http://localhost:8000/optimize \
  -H "Content-Type: application/json" \
  -d '{
    "apiKey": "your_openrouteservice_api_key",
    "addresses": [
      {"address1": "Start", "location": {"type": "Point", "coordinates": [-73.8955, 40.8515]}},
      {"address1": "Stop", "location": {"type": "Point", "coordinates": [-73.91335, 40.87995]}},
      {"address1": "End", "location": {"type": "Point", "coordinates": [-73.8955, 40.8515]}}
    ]
  }'
```

### Go client

```go
POST http://route-optimizer:8000/optimize
Content-Type: application/json

{"apiKey": "...", "addresses": [{...core.Address...}, ...]}
```

Optional container env vars:

| Variable | Default | Description |
|---|---|---|
| `ORS_API_KEY` | — | Fallback OpenRouteService API key when `apiKey` is omitted |
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
| `tests/test_main.py` | `/health`, `/optimize` with `Address` payloads, validation, and `routeOrder` |
| `tests/test_live_integration.py` | Live HTTP + optimizer calls using `addresses` (skipped without `ORS_API_KEY`) |

Unit tests mock external services and do not require an API key. Integration tests require `ORS_API_KEY` in `.env` and make real API calls to OpenRouteService.
