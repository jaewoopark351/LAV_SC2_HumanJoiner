# LAV SC2 Human Joiner

Minimal console prototype for a human player's PC on the same LAN as a LAV
StarCraft II host.

This project is separate from `LAV_v0.2`. It does not run LAV and does not
modify SC2AIApp or Sc2LadderServer.

## Requirements

- Windows
- Python 3.14
- StarCraft II installed on the human player's PC
- Standard library only for the console tool
- Optional Gradio dependency for the GUI

## Initial Setup

```bat
cd /d C:\Vtuber_Souorce_Code\StarCraft2\LAV_SC2_HumanJoiner
py -3.14 -m venv .venv
.venv\Scripts\activate
python --version
```

## What It Does

- Listens for LAV StarCraft II LAN lobby broadcasts on UDP port `47624`.
- Parses `lav.sc2.lan_room` payloads with protocol version `1`.
- Keeps one current entry per `room_id` and `source_id`.
- Removes rooms after their advertised TTL expires.
- Finds `SC2_x64.exe` on the human PC.
- Prints the command and environment that would be used later.

The console tool intentionally does not:

- install third-party packages,
- use `python-sc2` or `burnysc2`,
- automate mouse or keyboard input,
- package an executable.

The Gradio GUI can launch StarCraft II only when the user presses `Join Game`.
It does not automate mouse or keyboard input.

## Usage

```bat
python human_joiner.py scan
```

Optional scan settings:

```bat
python human_joiner.py scan --seconds 15 --port 47624
```

Check an advertised or manually entered host before launching:

```bat
python human_joiner.py check --host 192.168.0.67 --proxy-ports 5677,5678
```

By default, `check` and `join` use the first proxy port as the human slot
port. Use `--check-all-ports` only for diagnostics.

Launch StarCraft II for the first discovered room:

```bat
python human_joiner.py join --seconds 15
```

Launch StarCraft II when UDP discovery is blocked but you know the LAV host IP:

```bat
python human_joiner.py join --host 192.168.0.67 --proxy-ports 5677,5678
```

Example output:

```text
Found LAV StarCraft II room
Host: 192.168.0.67
Bot: Changeling
Map: IncorporealAIE_v4
Proxy ports: 5677,5678
SC2 executable: C:\Program Files (x86)\StarCraft II\Versions\Base97425\SC2_x64.exe

Launch preview
Command: "C:\Program Files (x86)\StarCraft II\Versions\Base97425\SC2_x64.exe"
Environment:
  LAV_SC2_PROXY_HOST=192.168.0.67
  LAV_SC2_PROXY_PORTS=5677,5678
  LAV_SC2_START_PORT=5690
  LAV_SC2_ROOM_ID=...
```

If StarCraft II is not found, the joiner still prints the room and reports that
the executable could not be located.

## Gradio GUI

Install the optional GUI dependency:

```bat
.venv\Scripts\activate
python -m pip install -r requirements-gradio.txt
```

Run the GUI:

```bat
python human_joiner_gui.py
```

The GUI provides:

- `Scan LAN` to find LAV StarCraft II rooms.
- `Check Proxy` to test the advertised TCP proxy ports.
- `Join Game` to launch `SC2_x64.exe` with the selected room environment.
- `Settings` to load, edit, and save the local `SC2_x64.exe` path.

The browser UI is local by default at `http://127.0.0.1:47860`.

Local GUI settings are stored in:

```text
config\human_joiner_config.json
```

Example:

```json
{
  "sc2_executable_path": "C:\\Program Files (x86)\\StarCraft II\\Versions\\Base97425\\SC2_x64.exe",
  "manual_host": "26.189.202.71",
  "manual_proxy_ports": "5677,5678",
  "manual_start_port": 5690,
  "manual_join_port": 47625
}
```

## LAN Lobby Payload

```json
{
  "protocol": "lav.sc2.lan_room",
  "version": 1,
  "source_id": "...",
  "room_id": "...",
  "room_name": "LAV StarCraft II",
  "host_name": "AI-PC",
  "player_name": "LAV",
  "mode": "observer",
  "preferred_bot": "Changeling",
  "preferred_map": "IncorporealAIE_v4",
  "proxy_host": "192.168.0.67",
  "proxy_ports": [5677, 5678],
  "start_port": 5690,
  "timestamp": 0,
  "expires_sec": 10
}
```

## Tests

```bat
python -m unittest
```
# LAV_SC2_HumanJoiner
