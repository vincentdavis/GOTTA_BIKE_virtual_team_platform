---
title: API Key Management
source: "https://zwiftgopher.com/api/dashboard.php"
saved: 2026-06-16
---

# API Key Management

## API Documentation

### Authentication

Include your API key in the Authorization header:

Authorization: Bearer sk\_live\_your\_key\_here

### Rate Limiting

**Limit:** 1 request per 60 seconds per key / IP address

The API returns rate limit information in response headers:

X-RateLimit-Limit: 1  
X-RateLimit-Remaining: 0  
X-RateLimit-Reset: 1234567890

### Endpoints

#### POST /api/optimize

Run auto-optimize on a team composition and get the optimized plan.

**Single teams and batch requests use the same route.** Existing single-request clients do not need to change endpoint paths.

**Request:**

{  
"request\_id": "team-a",  
"riders": \[354912, 6142432, 1909735\],  
"team\_name": "Saturday TTT",  
"route": "next\_wtrl",  
"intensity": 0  
}

**Rider input formats:**

-   **Array of IDs:** `[354912, 6142432, 1909735]`
-   **ID-to-overrides map (separate):** `"rider_overrides": { "354912": { "ftp": 239, "name": "Dave", "power_300_watts": 295 } }` (IDs must exist in `riders`)
-   **Manual riders:** `"custom_riders": [{ "name": "Guest", "ftp": 250, "weight": 75, "height": 178 }]`

**Optional SI inputs:**

-   `"power_300_watts": 347` if you already know 5-minute power in watts
-   `"power_300_wkg": 4.45` if you already know 5-minute power in W/kg
-   Both fields can be sent directly inside rider objects or inside `rider_overrides`
-   If omitted, SI falls back to `130% FTP`

**Example: direct rider objects with 300s power**

{  
"riders": \[  
{ "zwift\_id": "354912", "power\_300\_watts": 295 },  
{ "zwift\_id": "751042", "power\_300\_wkg": 4.5 }  
\]  
}

**Example: rider\_overrides with 300s power**

{  
"riders": \[354912, 751042\],  
"rider\_overrides": {  
"354912": { "ftp": 239, "power\_300\_watts": 295 },  
"751042": { "power\_300\_wkg": 4.5 }  
}  
}

#### Batch Mode

Use a top-level `requests` array when you want to optimize multiple teams in one API call. You can also use `defaults` for shared settings.

{  
"defaults": {  
"route": "next\_wtrl",  
"intensity": 1,  
"duration\_interval": 10  
},  
"requests": \[  
{  
"request\_id": "alpha",  
"team\_name": "Alpha",  
"riders": \[354912, 6142432, 1909735\]  
},  
{  
"request\_id": "beta",  
"team\_name": "Beta",  
"riders": \[5652740, 579296, 2765354\]  
}  
\]  
}

**Batch limits:** up to 20 optimize requests per batch. Each batch item succeeds or fails independently.

**Response:**

{  
"success": true,  
"data": {  
"request\_id": "team-a",  
"route": "Canopies and Coastlines",  
"estimated\_time\_seconds": 1947,  
"estimated\_avg\_speed": 43.2,  
"team\_avg\_power": 285,  
"riders": \[  
{  
"zwift\_id": "354912",  
"name": "Dave Edmonds",  
"power\_300\_watts": 295,  
"speed\_index": 68,  
"speed\_index\_source": "power\_profile",  
"...": "..."  
}  
\]  
},  
"meta": { ... }  
}

**Batch response:**

{  
"success": true,  
"data": {  
"mode": "batch",  
"results": \[  
{  
"index": 0,  
"request\_id": "alpha",  
"success": true,  
"data": { ... }  
},  
{  
"index": 1,  
"request\_id": "beta",  
"success": false,  
"error": "OPTIMIZE\_ERROR",  
"message": "At least 2 riders are required",  
"status\_code": 400  
}  
\],  
"summary": {  
"total\_requests": 2,  
"successful\_requests": 1,  
"failed\_requests": 1  
}  
}  
}

### Example Library

Choose a copy/paste starting point. These examples all use the same `POST /api/optimize` route.

Fastest single-team starting point. Send rider IDs and let the API fetch rider data.

Request JSON

{ "request\_id": "team-a", "route": "next\_wtrl", "team\_name": "Saturday TTT", "riders": \[354912, 6142432, 1909735\] }

```
curl -X POST https://zwiftgopher.com/api/optimize \
  -H "Authorization: Bearer sk_live_your_key_here" \
  -H "Content-Type: application/json" \
  -d '{
    "request_id": "team-a",
    "route": "next_wtrl",
    "team_name": "Saturday TTT",
    "riders": [354912, 6142432, 1909735]
  }'
```

Use IDs plus `rider_overrides` when you want to adjust FTP, name, body data, or 300-second power without sending a full rider object.

Request JSON

{ "request\_id": "team-a", "route": "next\_wtrl", "riders": \[354912, 4421933, 6142432\], "rider\_overrides": { "354912": { "ftp": 239, "name": "Dave", "power\_300\_watts": 295 }, "4421933": { "weight": 90, "height": 185, "power\_300\_wkg": 4.2 } } }

```
curl -X POST https://zwiftgopher.com/api/optimize \
  -H "Authorization: Bearer sk_live_your_key_here" \
  -H "Content-Type: application/json" \
  -d '{
    "request_id": "team-a",
    "route": "next_wtrl",
    "riders": [354912, 4421933, 6142432],
    "rider_overrides": {
      "354912": { "ftp": 239, "name": "Dave", "power_300_watts": 295 },
      "4421933": { "weight": 90, "height": 185, "power_300_wkg": 4.2 }
    }
  }'
```

Use `custom_riders` when you want to optimize riders that are not looked up from ZwiftRacing/ZwiftPower.

Request JSON

{ "request\_id": "manual-team", "route": "next\_wtrl", "team\_name": "Guest Squad", "custom\_riders": \[ { "name": "Guest Rider 1", "ftp": 260, "weight": 71, "height": 177, "power\_300\_watts": 335 }, { "name": "Guest Rider 2", "ftp": 245, "weight": 66, "height": 170, "power\_300\_watts": 315 } \] }

```
curl -X POST https://zwiftgopher.com/api/optimize \
  -H "Authorization: Bearer sk_live_your_key_here" \
  -H "Content-Type: application/json" \
  -d '{
    "request_id": "manual-team",
    "route": "next_wtrl",
    "team_name": "Guest Squad",
    "custom_riders": [
      { "name": "Guest Rider 1", "ftp": 260, "weight": 71, "height": 177, "power_300_watts": 335 },
      { "name": "Guest Rider 2", "ftp": 245, "weight": 66, "height": 170, "power_300_watts": 315 }
    ]
  }'
```

Basic batch request for someone managing multiple teams. Shared settings live in `defaults`.

Request JSON

{ "defaults": { "route": "next\_wtrl", "intensity": 1, "duration\_interval": 10 }, "requests": \[ { "request\_id": "alpha", "team\_name": "Alpha", "riders": \[354912, 751042, 4214146, 5339496\] }, { "request\_id": "beta", "team\_name": "Beta", "riders": \[5652740, 579296, 2765354, 987271\] } \] }

```
curl -X POST https://zwiftgopher.com/api/optimize \
  -H "Authorization: Bearer sk_live_your_key_here" \
  -H "Content-Type: application/json" \
  -d '{
    "defaults": {
      "route": "next_wtrl",
      "intensity": 1,
      "duration_interval": 10
    },
    "requests": [
      {
        "request_id": "alpha",
        "team_name": "Alpha",
        "riders": [354912, 751042, 4214146, 5339496]
      },
      {
        "request_id": "beta",
        "team_name": "Beta",
        "riders": [5652740, 579296, 2765354, 987271]
      }
    ]
  }'
```

Mixed batch example. The first team uses IDs plus overrides. The second team uses manual riders. This is the best example when teams are managed differently.

Request JSON

{ "defaults": { "route": "next\_wtrl", "optimization\_strategy": "variable", "intensity": 0 }, "requests": \[ { "request\_id": "wtrl-a", "team\_name": "WTRL A", "riders": \[354912, 751042, 4214146, 5339496\], "rider\_overrides": { "354912": { "ftp": 239, "name": "Dave", "power\_300\_watts": 295 }, "4214146": { "power\_300\_wkg": 4.6 } } }, { "request\_id": "guest-team", "team\_name": "Guest Team", "custom\_riders": \[ { "name": "Manual Rider 1", "ftp": 255, "weight": 69, "height": 175, "power\_300\_watts": 330 }, { "name": "Manual Rider 2", "ftp": 242, "weight": 73, "height": 179, "power\_300\_watts": 312 }, { "name": "Manual Rider 3", "ftp": 235, "weight": 64, "height": 168, "power\_300\_watts": 305 } \] } \] }

```
curl -X POST https://zwiftgopher.com/api/optimize \
  -H "Authorization: Bearer sk_live_your_key_here" \
  -H "Content-Type: application/json" \
  -d '{
    "defaults": {
      "route": "next_wtrl",
      "optimization_strategy": "variable",
      "intensity": 0
    },
    "requests": [
      {
        "request_id": "wtrl-a",
        "team_name": "WTRL A",
        "riders": [354912, 751042, 4214146, 5339496],
        "rider_overrides": {
          "354912": { "ftp": 239, "name": "Dave", "power_300_watts": 295 },
          "4214146": { "power_300_wkg": 4.6 }
        }
      },
      {
        "request_id": "guest-team",
        "team_name": "Guest Team",
        "custom_riders": [
          { "name": "Manual Rider 1", "ftp": 255, "weight": 69, "height": 175, "power_300_watts": 330 },
          { "name": "Manual Rider 2", "ftp": 242, "weight": 73, "height": 179, "power_300_watts": 312 },
          { "name": "Manual Rider 3", "ftp": 235, "weight": 64, "height": 168, "power_300_watts": 305 }
        ]
      }
    ]
  }'
```

Batch example with explicit SI hints. Useful when your own tooling already knows 300-second watts or W/kg and you want to avoid relying on fetched power profile data.

Request JSON

{ "defaults": { "route": "next\_wtrl", "efficiency": 1 }, "requests": \[ { "request\_id": "alpha-si", "team\_name": "Alpha SI", "riders": \[ { "zwift\_id": "354912", "power\_300\_watts": 295 }, { "zwift\_id": "751042", "power\_300\_wkg": 4.5 } \] }, { "request\_id": "beta-si", "team\_name": "Beta SI", "riders": \[ { "zwift\_id": "5652740", "power\_300\_watts": 365 }, { "zwift\_id": "579296", "power\_300\_wkg": 4.7 } \] } \] }

```
curl -X POST https://zwiftgopher.com/api/optimize \
  -H "Authorization: Bearer sk_live_your_key_here" \
  -H "Content-Type: application/json" \
  -d '{
    "defaults": {
      "route": "next_wtrl",
      "efficiency": 1
    },
    "requests": [
      {
        "request_id": "alpha-si",
        "team_name": "Alpha SI",
        "riders": [
          { "zwift_id": "354912", "power_300_watts": 295 },
          { "zwift_id": "751042", "power_300_wkg": 4.5 }
        ]
      },
      {
        "request_id": "beta-si",
        "team_name": "Beta SI",
        "riders": [
          { "zwift_id": "5652740", "power_300_watts": 365 },
          { "zwift_id": "579296", "power_300_wkg": 4.7 }
        ]
      }
    ]
  }'
```

### Notes:

**Notes on rider data:** Rider metadata (FTP, weight, height, name) is fetched from ZwiftRacing and ZwiftPower when not supplied explicitly. We recommend a script timeout (execution limit) of around 90 seconds to allow for data fetching and optimisation. Please post feedback, support and feature requests to the Discord server.

## Optimization Settings Reference

The API supports various settings to customize the optimization behavior. All settings are optional and use sensible defaults.

### Request Body Structure

{  
"request\_id": "team-a",  
"riders": \[ ... \],  
"rider\_overrides": { ... },  
"custom\_riders": \[ ... \],  
"route": "next",  
"team\_name": "API Team",  
"target\_speed": 40,  
"intensity": 0,  
"efficiency": 0,  
"allow\_zero\_pulls": false,  
"min\_pull\_duration": 30,  
"max\_pull\_duration": 180,  
"duration\_interval": 15,  
"optimization\_strategy": "variable"  
}

### Batch Request Structure

{  
"defaults": {  
"route": "next\_wtrl",  
"intensity": 1  
},  
"requests": \[  
{  
"request\_id": "alpha",  
"team\_name": "Alpha",  
"riders": \[ ... \]  
},  
{  
"request\_id": "beta",  
"team\_name": "Beta",  
"riders": \[ ... \]  
}  
\]  
}

### Rider Limits

Current Build: 8 Riders Maximum

Default limit is 8 riders per optimization

**Future Enhancement:** A `max_riders` parameter will allow override up to 12 riders maximum (requires authentication upgrade)

**Batch Mode:** Up to `20` optimize requests can be included in a single batch call.

### Available Settings

#### request\_id

<table><tbody><tr><td><strong>Type</strong></td><td>String</td></tr><tr><td><strong>Default</strong></td><td>Not set</td></tr><tr><td><strong>Description</strong></td><td>Optional client correlation ID echoed back in the response. Especially useful for batch mode.</td></tr></tbody></table>

#### riders

<table><tbody><tr><td><strong>Type</strong></td><td>Array (IDs only)</td></tr><tr><td><strong>Required</strong></td><td>No (but custom_riders must be populated instead)</td></tr><tr><td><strong>Description</strong></td><td>Provide Zwift rider IDs directly. Use <code>rider_overrides</code> for overrides.</td></tr></tbody></table>

**Overrides supported:** name, ftp, weight, height, adjustment, power\_300\_watts, power\_300\_wkg.

**Optional SI fields:** `power_300_watts` or `power_300_wkg`. If these are omitted, SI falls back to `130% FTP`.

#### defaults

<table><tbody><tr><td><strong>Type</strong></td><td>Object</td></tr><tr><td><strong>Required</strong></td><td>No</td></tr><tr><td><strong>Description</strong></td><td>Batch-only object for shared settings. Values here are applied to every item in <code>requests</code>, unless that item overrides them.</td></tr></tbody></table>

#### requests

<table><tbody><tr><td><strong>Type</strong></td><td>Array of optimize request objects</td></tr><tr><td><strong>Required</strong></td><td>No (used only for batch mode)</td></tr><tr><td><strong>Max Items</strong></td><td>20</td></tr><tr><td><strong>Description</strong></td><td>Switches the endpoint into batch mode. Each item uses the same request shape as a normal single optimize call.</td></tr></tbody></table>

#### custom\_riders

<table><tbody><tr><td><strong>Type</strong></td><td>Array of objects</td></tr><tr><td><strong>Required Fields</strong></td><td>name, ftp, weight, height</td></tr><tr><td><strong>Description</strong></td><td>Manual riders without Zwift IDs. Each entry must include all four fields.</td></tr></tbody></table>

#### team\_name

<table><tbody><tr><td><strong>Type</strong></td><td>String</td></tr><tr><td><strong>Default</strong></td><td>"API Team"</td></tr><tr><td><strong>Description</strong></td><td>Optional display name for the team in results.</td></tr></tbody></table>

#### route

<table><tbody><tr><td><strong>Type</strong></td><td>String</td></tr><tr><td><strong>Default</strong></td><td>"next"</td></tr><tr><td><strong>Valid Values</strong></td><td>"next", "next_wtrl", "next_zrl"</td></tr><tr><td><strong>Description</strong></td><td>Select which upcoming event schedule to use. wtrl refers to the Thursday TTT, zrl refers to the Zwift Racing League.</td></tr></tbody></table>

#### intensity

<table><tbody><tr><td><strong>Type</strong></td><td>Integer (-3 to +3)</td></tr><tr><td><strong>Default</strong></td><td>0 (neutral)</td></tr><tr><td><strong>Description</strong></td><td>Adjusts the effort intensity of the optimization. Negative values reduce intensity/effort, positive values increase it.</td></tr><tr><td><strong>Valid Range</strong></td><td>-3 (easiest) to +3 (hardest)</td></tr></tbody></table>

#### efficiency

<table><tbody><tr><td><strong>Type</strong></td><td>Integer</td></tr><tr><td><strong>Default</strong></td><td>0 (neutral)</td></tr><tr><td><strong>Valid Range</strong></td><td>-2 to +2</td></tr><tr><td><strong>Description</strong></td><td>Scales the efficiency of power distribution. -2: average team efficiency (gaps, overlapping, messy rotations), +2: elite efficiency (perfect pace lines).</td></tr></tbody></table>

#### allow\_zero\_pulls

<table><tbody><tr><td><strong>Type</strong></td><td>Boolean</td></tr><tr><td><strong>Default</strong></td><td>false (disabled)</td></tr><tr><td><strong>Description</strong></td><td>When disabled, every rider must pull for at least the minimum duration. When enabled, riders can be designated as non-pullers (useful for recovering or weaker riders).</td></tr></tbody></table>

#### min\_pull\_duration

<table><tbody><tr><td><strong>Type</strong></td><td>Integer (seconds)</td></tr><tr><td><strong>Default</strong></td><td>30 seconds</td></tr><tr><td><strong>Valid Range</strong></td><td>10 to 120 seconds</td></tr><tr><td><strong>Description</strong></td><td>The minimum duration each pull must last. Shorter minimums allow more frequent rider changes.</td></tr></tbody></table>

#### max\_pull\_duration

<table><tbody><tr><td><strong>Type</strong></td><td>Integer (seconds)</td></tr><tr><td><strong>Default</strong></td><td>180 seconds (3 minutes)</td></tr><tr><td><strong>Valid Range</strong></td><td>30 to 600 seconds</td></tr><tr><td><strong>Description</strong></td><td>The maximum duration a single pull can last. Shorter maximums force more rotation.</td></tr></tbody></table>

#### duration\_interval

<table><tbody><tr><td><strong>Type</strong></td><td>Integer (seconds)</td></tr><tr><td><strong>Default</strong></td><td>15 seconds</td></tr><tr><td><strong>Valid Range</strong></td><td>10 or 15 seconds only</td></tr><tr><td><strong>Description</strong></td><td>Time interval used for split calculations in the optimization algorithm.</td></tr></tbody></table>

#### optimization\_strategy

<table><tbody><tr><td><strong>Type</strong></td><td>String</td></tr><tr><td><strong>Default</strong></td><td>"variable"</td></tr><tr><td><strong>Valid Range</strong></td><td>"variable" or "fixed"</td></tr><tr><td><strong>Description</strong></td><td><strong>"variable":</strong> Optimizer adjusts speed based on rider capability and power dynamics<br><strong>"fixed":</strong> Optimizes to a fixed pull speed (on the flat) for the whole team</td></tr></tbody></table>

### Example: Complete Single Request

```
{
    "request_id": "team-a",
    "riders": [354912, 4421933, 6142432],
    "rider_overrides": {
        "354912": { "ftp": 239, "name": "Dave", "power_300_watts": 295 },
        "4421933": { "weight": 90, "height": 185, "power_300_wkg": 4.2 }
    },
    "team_name": "API Test Team",
    "target_speed": 42,
    "intensity": 1,
    "efficiency": 1,
    "allow_zero_pulls": false,
    "min_pull_duration": 30,
    "max_pull_duration": 180,
    "duration_interval": 15,
    "optimization_strategy": "variable"
}
```

### Example: Complete Batch Request

```
{
    "defaults": {
        "route": "next_wtrl",
        "intensity": 1,
        "efficiency": 1,
        "duration_interval": 15
    },
    "requests": [
        {
            "request_id": "alpha",
            "team_name": "Alpha",
            "riders": [354912, 751042, 4214146, 5339496]
        },
        {
            "request_id": "beta",
            "team_name": "Beta",
            "riders": [5652740, 579296, 2765354, 987271]
        }
    ]
}
```
