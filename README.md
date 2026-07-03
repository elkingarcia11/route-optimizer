# Route Optimizer

Reorder a list of stops to minimize total travel distance on real roads, with a fixed start and end point.

Given coordinates for a **start**, several **stops**, and an **end**, the script finds the best order to visit every stop exactly once while keeping the start and end fixed.

## How it works

1. **OpenRouteService** builds a road distance matrix (meters) between all locations.
2. **Google OR-Tools** solves the routing problem and returns the optimized visit order.

Distances are based on actual road networks, not straight-line (haversine) distance.

## Requirements

- Python 3.10+
- A free [OpenRouteService API key](https://openrouteservice.org/dev/#/signup)

## Installation

```bash
git clone <repo-url>
cd route-optimizer

python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## API key

Copy the example env file and add your key:

```bash
cp .env.example .env
```

Edit `.env`:

```
ORS_API_KEY=your_actual_api_key
```

The script loads this automatically. You can also pass `--api-key` or set the `ORS_API_KEY` environment variable directly.

## Usage

Coordinates use **`latitude,longitude`** format.

### Command line

```bash
python route_optimizer.py \
  --start "40.7128,-74.0060" \
  --stop "40.7580,-73.9855" \
  --stop "40.7484,-73.9857" \
  --stop "40.7614,-73.9776" \
  --end "40.7308,-73.9973"
```

### JSON input file

Create `route.json`. Each location can include address fields for labeling (only `lat`/`lng` are used for routing):

```json
{
  "start": {
    "lat": 40.7128,
    "lng": -74.0060,
    "street_address_1": "123 Main St",
    "city": "New York",
    "state": "NY",
    "zip": "10001"
  },
  "stops": [
    {
      "lat": 40.7580,
      "lng": -73.9855,
      "street_address_1": "456 Broadway",
      "city": "New York",
      "state": "NY",
      "zip": "10012"
    },
    {
      "lat": 40.7484,
      "lng": -73.9857,
      "street_address_1": "789 5th Ave",
      "city": "New York",
      "state": "NY",
      "zip": "10022"
    }
  ],
  "end": {
    "lat": 40.7308,
    "lng": -73.9973,
    "street_address_1": "321 Park Ave",
    "city": "New York",
    "state": "NY",
    "zip": "10010"
  },
  "profile": "driving-car",
  "time_limit_seconds": 5
}
```

Legacy `[lat, lng]` arrays are still supported for start, end, and stops.

Run:

```bash
python route_optimizer.py --input route.json
```

### Python module

```python
from route_optimizer import Location, optimize_route

result = optimize_route(
    start=Location(
        40.7128, -74.0060,
        street_address_1="123 Main St",
        city="New York",
        state="NY",
        zip="10001",
    ),
    stops=[
        Location(40.7580, -73.9855, street_address_1="456 Broadway", city="New York", state="NY", zip="10012"),
    ],
    end=Location(40.7308, -73.9973, street_address_1="321 Park Ave", city="New York", state="NY", zip="10010"),
    api_key="your_api_key",
    profile="driving-car",
)

print(result["stop_order"])            # original stop indices in optimized order
print(result["ordered_locations"])     # full path with address labels
print(result["total_distance_meters"]) # total road distance
```

## Output

The script prints JSON:

```json
{
  "ordered_locations": [
    {
      "lat": 40.7128,
      "lng": -74.006,
      "street_address_1": "123 Main St",
      "city": "New York",
      "state": "NY",
      "zip": "10001",
      "label": "123 Main St, New York, NY 10001"
    }
  ],
  "ordered_coordinates": [[40.7128, -74.006], ...],
  "ordered_indices": [0, 2, 3, 1, 4],
  "stop_order": [1, 2, 0],
  "total_distance_meters": 9858,
  "distance_source": "openrouteservice",
  "profile": "driving-car"
}
```

| Field | Description |
|---|---|
| `ordered_locations` | Full route in visit order with coordinates and address labels |
| `ordered_coordinates` | Coordinates only, in visit order |
| `stop_order` | Original stop list indices in optimized order |
| `total_distance_meters` | Total road distance along the route |
| `profile` | OpenRouteService travel profile used |

Each location in `ordered_locations` includes `street_address_1`, `city`, `state`, `zip`, and a formatted `label`.

## Options

| Flag | Default | Description |
|---|---|---|
| `--start` | — | Start coordinate as `lat,lng` |
| `--stop` | — | Stop coordinate (repeat for each stop) |
| `--end` | — | End coordinate as `lat,lng` |
| `--input`, `-i` | — | JSON input file |
| `--api-key` | from `.env` | OpenRouteService API key |
| `--profile` | `driving-car` | Travel mode (see below) |
| `--time-limit` | `5` | OR-Tools solver time limit (seconds) |

### Travel profiles

Common OpenRouteService profiles:

- `driving-car` (default)
- `driving-hgv`
- `foot-walking`
- `cycling-regular`

See the [OpenRouteService docs](https://openrouteservice.org/dev/#/api-docs) for the full list.
