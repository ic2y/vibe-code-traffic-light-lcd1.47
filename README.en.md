# vibe code Traffic Light

[中文版](README.md) | **English**

A traffic-light status service built on a 1.47-inch serial display (160×80). Search the Taobao store 【墨砺工作室】.

The screen is split into left and right zones by a vertical divider line. Each zone has two rectangular "lights" stacked vertically — red on top, green on bottom — with a letter label always shown inside each light.

- The **left** light shows **OP** — for **OpenCode**
- The **right** light shows **CC** — for **Claude Code**

Rules: within a zone, red and green are mutually exclusive (when red is on, green is off, and vice versa); the left and right zones are independent.

Preview:

![led.gif](led.gif)

## Screen Layout

```
┌──────────────────────────────────────────────┬──────────────────────────────────────────────┐
│                                             │                                              │
│   ┌──────────────────────────────────┐      │      ┌──────────────────────────────────┐      │
│   │  OP                    (Red)     │      │      │  CC                    (Red)     │      │
│   └──────────────────────────────────┘      │      └──────────────────────────────────┘      │
│   ┌──────────────────────────────────┐      │      ┌──────────────────────────────────┐      │
│   │  OP                    (Green)   │      │      │  CC                    (Green)   │      │
│   └──────────────────────────────────┘      │      └──────────────────────────────────┘      │
│                                             │                                              │
└──────────────────────────────────────────────┴──────────────────────────────────────────────┘
```

- Gray outlined rectangles are the light housings, **always visible**
- The white letters inside (OP on the left / CC on the right) are **always visible**
- When lit, the interior is filled with red or dark green

## Run

```bash
# Dependencies
pip install -r requirements.txt          # flask, pyserial

# Real device mode (auto-detects and connects to the MSU2 serial port)
python http_server.py

# Simulation mode (no hardware required, for integration/testing)
LED_SIMULATE=1 python http_server.py
```

Host and port can be overridden via env vars: `LED_HOST` (default `0.0.0.0`), `LED_PORT` (default `15000`).

## Public API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/led/left/green/on` | Left green on (left red turns off automatically) |
| GET | `/led/left/red/on` | Left red on (left green turns off automatically) |
| GET | `/led/right/red/on` | Right red on (right green turns off automatically) |
| GET | `/led/right/green/on` | Right green on (right red turns off automatically) |
| GET | `/led/all/off` | Turn off all lights |
| GET | `/status` | Query current state of both zones |
| GET | `/health` | Health check |

All endpoints return JSON. Each command below shows the actual response and the on-screen effect.

> Note: the `message` field in responses is returned in Chinese; it is translated to English below for readability.

### Turn on Left Green

```bash
curl http://127.0.0.1:15000/led/left/green/on
```

Response:

```json
{"left":{"green":true,"red":false},"message":"Left green light is on","right":{"green":false,"red":true},"success":true}
```

On-screen: the lower half of the left zone fills green, the upper half of the left zone turns off; the right zone stays unchanged.

### Turn on Left Red

```bash
curl http://127.0.0.1:15000/led/left/red/on
```

Response:

```json
{"left":{"green":false,"red":true},"message":"Left red light is on","right":{"green":false,"red":true},"success":true}
```

On-screen: the upper half of the left zone fills red (over the left green); the right zone stays unchanged.

### Turn on Right Red

```bash
curl http://127.0.0.1:15000/led/right/red/on
```

Response:

```json
{"left":{"green":false,"red":true},"message":"Right red light is on","right":{"green":false,"red":true},"success":true}
```

On-screen: the upper half of the right zone fills red; the left zone stays unchanged.

### Turn on Right Green

```bash
curl http://127.0.0.1:15000/led/right/green/on
```

Response:

```json
{"left":{"green":false,"red":true},"message":"Right green light is on","right":{"green":true,"red":false},"success":true}
```

On-screen: the lower half of the right zone fills green (over the right red); the left zone stays unchanged.

### Turn Off All Lights

```bash
curl http://127.0.0.1:15000/led/all/off
```

Response:

```json
{"message":"All lights are off","success":true}
```

On-screen: all four lights go dark, leaving only the gray housings and the OP/CC letters.

### Query Status

```bash
curl http://127.0.0.1:15000/status
```

Response:

```json
{"left":{"green":false,"red":false},"mode":"real","right":{"green":false,"red":false}}
```

The `mode` field: `real` means real serial-port mode, `simulate` means simulation mode.

### Health Check

```bash
curl http://127.0.0.1:15000/health
```

Response:

```json
{"service":"LED Display Service","status":"ok"}
```

## A Complete Demo

```bash
# 1. All off
curl http://127.0.0.1:15000/led/all/off
# 2. Left green + right red (the classic "go/stop" combination)
curl http://127.0.0.1:15000/led/left/green/on
curl http://127.0.0.1:15000/led/right/red/on
# 3. Left red + right green (swapped)
curl http://127.0.0.1:15000/led/left/red/on
curl http://127.0.0.1:15000/led/right/green/on
# 4. All off
curl http://127.0.0.1:15000/led/all/off
```

## Claude Code Automatic Hooks (LED Status Light)

Claude Code can run **hooks** — external commands triggered automatically on specific events — via a `.claude/settings.json` file. The configuration below turns the right-side red/green light into a "working status" indicator:

- **When you submit a prompt** (`UserPromptSubmit`) → right red light turns on (Claude is working)
- **When Claude finishes responding** (`Stop`) → right green light turns on (this round is done)

### Setup

Where you put the config determines how widely the hooks apply — choose one of the two:

**Option 1: This project only (recommended)**

Create a `.claude` folder and `settings.json` under the project root:

- Windows: `d:\code\play\led\.claude\settings.json`
- macOS / Linux: `<project root>/.claude/settings.json`

Only Claude Code sessions that start with this directory as the working directory will read it.

**Option 2: Global (applies to every project)**

Create a `.claude` folder and `settings.json` under your user home directory:

- Windows: `C:\Users\<your username>\.claude\settings.json`
- macOS / Linux: `~/.claude/settings.json`

Every Claude Code session reads it, regardless of project. Note: with a global config the LED service must keep running, otherwise every project triggers the requests (already swallowed silently by `.catch`, so no real impact).

In either case the `settings.json` content is the same:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "node -e \"fetch('http://127.0.0.1:15000/led/right/red/on').catch(()=>{})\"",
            "async": true
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "node -e \"fetch('http://127.0.0.1:15000/led/right/green/on').catch(()=>{})\"",
            "async": true
          }
        ]
      }
    ]
  }
}
```

2. Restart Claude Code (or start a new session) for the hooks to take effect.

### Notes

- `type: "command"`: the hook is executed as a shell command.
- `command`: uses Node's built-in `fetch` to call the LED turn-on endpoint (available in Node 18+, no extra dependencies). `127.0.0.1:15000` is the default local listen address; change it to the server IP when used remotely.
- `async: true`: runs asynchronously, so it never blocks the normal Claude Code flow and its output is not shown in the UI.
- `.catch(()=>{})`: swallows request errors — the hook won't fail even if the LED service is not running.
- Prerequisites: the LED service must be running locally (`python http_server.py`).
- If you used Option 1 but the hooks don't fire, confirm the current session's working directory is this project root; restart Claude Code (or start a new session) after changing the config so it gets reloaded.
- When both configs exist, the project-level one takes precedence — it overrides same-name hooks in the user-level config.

## OpenCode Plugin (LED Status Light)

OpenCode loads plugins from the `~/.config/opencode/plugins/` directory. The plugin below turns the left-side (OP) red/green light into a "working status" indicator:

- **When you send a message** (`chat.message`) → left red light turns on (OpenCode is working)
- **When the session becomes idle** (`session.idle`) → left green light turns on (this round is done)

### Setup

Create `led-notify.js` under `~/.config/opencode/plugins/` (create the directory if it doesn't exist) with the following content:

```js
export const LedNotifyPlugin = async () => {
  return {
    // user sends a message → red light
    "chat.message": async () => {
      try {
        await fetch("http://192.168.250.225:15000/led/left/red/on");
      } catch {}
    },
    // session idle (round finished) → green light
    event: async ({ event }) => {
      if (event.type === "session.idle") {
        try {
          await fetch("http://192.168.250.225:15000/led/left/green/on");
        } catch {}
      }
    },
  };
};
```

Restart OpenCode (or start a new session) for the plugin to take effect.

### Notes

- `chat.message`: fires on every message you send — lights the left red light.
- `event`: subscribes to OpenCode session events; `session.idle` means the round is over — lights the left green light.
- The address `192.168.250.225:15000` is an example remote address; change it to your actual LED service address (use `127.0.0.1:15000` for local deployment).
- `try/catch` silently swallows request failures — the plugin won't error even if the LED service is not running.
- Complements the Claude Code hooks: left (OP) drives OpenCode, right (CC) drives Claude Code — you can use both at the same time.

## Tests

```bash
# Start the service first, then run the test script (the HOST variable can be changed to a remote server address)
python test_led.py
```

Test coverage: all off initially, single light on, red/green exclusivity, left/right independence, cross-zone exclusivity, idempotent repeated turns-on, invalid arguments, etc.

## Notes

- The service includes an anti-sleep redraw thread that redraws the current frame every 2 seconds without clearing the screen — keeping the serial port active to suppress the device's auto-sleep, while avoiding black-screen flicker.
- Circle drawing behaved abnormally on the physical device, so the light bodies were changed to rectangles, which render stably with simple solid fills.
