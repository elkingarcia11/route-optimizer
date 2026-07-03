# Route Optimizer

Split stops across one or more routes balanced by road distance, or optimize a single route between a fixed start and end.

Given **start**, **end**, and at least **two stops** (HTTP API) or coordinates (CLI), the service finds the best visit order on real roads using OpenRouteService and Google OR-Tools.

## How it works

1. **OpenRouteService** builds a road distance matrix (meters) between all locations.
2. **Google OR-Tools** solves the routing problem:
   - **`numRoutes: 1`** — single route from start through all stops to end.
   - **`numRoutes` > 1** — multi-route VRP from a shared depot (start and end must match), splitting stops and balancing driving distance per route.

Distances are based on actual road networks, not straight-line distance.

## Project layout

| File | Purpose |
|---|---|
| `main.py` | FastAPI HTTP service (Docker entrypoint) |
| `route_optimizer.py` | Core optimizer + CLI |
| `route.json` | Example CLI input with addresses |
| `examples/` | Sample HTTP request/response JSON payloads |
| `tests/test_route_optimizer.py` | Core optimizer + CLI unit tests |
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

`apiKey` is **required in every** request body (`POST /routes/single` and `POST /routes/balance`). Callers pass their OpenRouteService key on each request so keys can be rotated without redeploying:

```json
{
  "apiKey": "your_openrouteservice_api_key",
  "start": { ... },
  "end": { ... },
  "stops": [ ... ],
  "numRoutes": 1
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

On a shared Docker network, other services (e.g. address-mapper or a Go API) reach it at:

```
http://route-optimizer:8000/routes/single
```

Set `ROUTE_OPTIMIZER_URL` in the caller if the host differs.

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Health check |
| `/routes/single` | POST | One optimized route (start → stops → end) |
| `/routes/balance` | POST | Split stops across routes balanced by distance |
| `/docs` | GET | Swagger UI (interactive API docs) |
| `/redoc` | GET | ReDoc API reference |
| `/openapi.json` | GET | OpenAPI 3 schema |

Sample request/response JSON files live in [`examples/`](examples/).

### Quick start with curl

Health check:

```bash
curl http://localhost:8000/health
```

Single route (see [`examples/single-route.request.json`](examples/single-route.request.json)):

```bash
curl -X POST http://localhost:8000/routes/single \
  -H "Content-Type: application/json" \
  -d @examples/single-route.request.json
```

Balanced multi-route (see [`examples/balance-routes.request.json`](examples/balance-routes.request.json)):

```bash
curl -X POST http://localhost:8000/routes/balance \
  -H "Content-Type: application/json" \
  -d @examples/balance-routes.request.json
```

Replace `your_openrouteservice_api_key` in the example files with your real key before running.

Expected response shapes: [`examples/single-route.response.json`](examples/single-route.response.json), [`examples/balance-routes.response.json`](examples/balance-routes.response.json).

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

Response addresses include all input fields plus `routeOrder` (1-based visit position within that route).

### Request — `POST /routes/single`

| Field | Type | Required | Description |
|---|---|---|---|
| `apiKey` | string | yes | OpenRouteService API key |
| `start` | Address | yes | Route start |
| `end` | Address | yes | Route end |
| `stops` | Address[] | yes | At least **two** stop addresses |

```json
{
  "apiKey": "your_openrouteservice_api_key",
  "start": { "address1": "2249 Washington Ave", "location": { "type": "Point", "coordinates": [-73.8955, 40.8515] } },
  "end": { "address1": "1101 Forest Ave", "location": { "type": "Point", "coordinates": [-73.90774, 40.88467] } },
  "stops": [
    { "address1": "1 Adrian Ave", "location": { "type": "Point", "coordinates": [-73.91335, 40.87995] } },
    { "address1": "125 W 228th St", "location": { "type": "Point", "coordinates": [-73.90774, 40.88467] } }
  ]
}
```

### Request — `POST /routes/balance`

| Field | Type | Required | Description |
|---|---|---|---|
| `apiKey` | string | yes | OpenRouteService API key |
| `start` | Address | yes | Depot (same as `end`) |
| `end` | Address | yes | Depot (same as `start`) |
| `stops` | Address[] | yes | At least **two** stop addresses |
| `numRoutes` | integer | yes | At least **2** routes |

```json
{
  "apiKey": "your_openrouteservice_api_key",
  "start": { "address1": "2249 Washington Ave", "location": { "type": "Point", "coordinates": [-73.8955, 40.8515] } },
  "end": { "address1": "2249 Washington Ave", "location": { "type": "Point", "coordinates": [-73.8955, 40.8515] } },
  "stops": [
    { "address1": "1 Adrian Ave", "location": { "type": "Point", "coordinates": [-73.91335, 40.87995] } },
    { "address1": "1101 Forest Ave", "location": { "type": "Point", "coordinates": [-73.90774, 40.88467] } }
  ],
  "numRoutes": 2
}
```

`location.coordinates` is GeoJSON order: **`[longitude, latitude]`**.

Optional address fields: `address2`, `apartment`, `country`, and `verification` (`is_verified`, `verified_at`).

### Response

```json
{
  "routes": [
    {
      "routeNumber": 1,
      "addresses": [
        {
          "address1": "2249 Washington Ave",
          "city": "Bronx",
          "state": "NY",
          "location": {
            "type": "Point",
            "coordinates": [-73.8955, 40.8515]
          },
          "routeOrder": 1
        },
        {
          "address1": "1 Adrian Ave",
          "location": {
            "type": "Point",
            "coordinates": [-73.91335, 40.87995]
          },
          "routeOrder": 2
        },
        {
          "address1": "2249 Washington Ave",
          "location": {
            "type": "Point",
            "coordinates": [-73.8955, 40.8515]
          },
          "routeOrder": 3
        }
      ],
      "distanceMeters": 8500
    }
  ],
  "numRoutes": 1,
  "totalDistanceMeters": 8500
}
```

| Field | Description |
|---|---|
| `routes` | Optimized routes in order |
| `routes[].routeNumber` | 1-based route identifier |
| `routes[].addresses` | Addresses in visit order for that route |
| `routes[].distanceMeters` | Road distance for that route in meters |
| `routeOrder` | 1-based position within the route |
| `numRoutes` | Number of routes returned |
| `totalDistanceMeters` | Combined road distance across all routes |

### curl examples

Single route:

```bash
curl -X POST http://localhost:8000/routes/single \
  -H "Content-Type: application/json" \
  -d @examples/single-route.request.json
```

Balanced multi-route:

```bash
curl -X POST http://localhost:8000/routes/balance \
  -H "Content-Type: application/json" \
  -d @examples/balance-routes.request.json
```

### Go client

```go
POST http://route-optimizer:8000/routes/single
Content-Type: application/json

{
  "apiKey": "...",
  "start": {...core.Address...},
  "end": {...core.Address...},
  "stops": [{...core.Address...}, ...]
}
```

```go
POST http://route-optimizer:8000/routes/balance
Content-Type: application/json

{
  "apiKey": "...",
  "start": {...core.Address...},
  "end": {...core.Address...},
  "stops": [{...core.Address...}, ...],
  "numRoutes": 2
}
```

Optional container env vars:

| Variable | Default | Description |
|---|---|---|
| `ROUTE_OPTIMIZER_PROFILE` | `driving-car` | ORS travel profile |
| `ROUTE_OPTIMIZER_TIME_LIMIT` | `5` | OR-Tools solver time limit for single-route mode (seconds) |

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
python route_optimizer.py --input route.json
```

### Python module

```python
from route_optimizer import Location, optimize_balanced_multi_route, optimize_route

# Single route
result = optimize_route(
    start=Location(40.8515, -73.8955, street_address_1="2249 Washington Ave", city="Bronx", state="NY"),
    stops=[
        Location(40.87995, -73.91335, street_address_1="1 Adrian Ave", city="Bronx", state="NY", zip="10463"),
        Location(40.88467, -73.90774, street_address_1="125 W 228th St", city="Bronx", state="NY"),
    ],
    end=Location(40.8515, -73.8955, street_address_1="2249 Washington Ave", city="Bronx", state="NY"),
    api_key="your_api_key",
)

# Multi-route, balanced by distance
depot = Location(40.8515, -73.8955, street_address_1="2249 Washington Ave", city="Bronx", state="NY")
multi = optimize_balanced_multi_route(
    depot,
    [
        Location(40.87995, -73.91335, street_address_1="1 Adrian Ave"),
        Location(40.88467, -73.90774, street_address_1="125 W 228th St"),
    ],
    num_routes=2,
    api_key="your_api_key",
)

print(result["ordered_locations"])
print(multi["routes"])
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
| `tests/test_route_optimizer.py` | `Location`, distance matrix, single- and multi-route optimization, CLI helpers |
| `tests/test_main.py` | `/health`, `/routes/single`, `/routes/balance`, validation, and responses |
| `tests/test_live_integration.py` | Live HTTP + optimizer calls (skipped without `ORS_API_KEY` in `.env`) |

Unit tests mock external services and do not require an API key. Integration tests read `ORS_API_KEY` from `.env` for live OpenRouteService calls and pass it as `apiKey` in HTTP request payloads.
