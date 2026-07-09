# LAV SC2 Human Joiner

[English](README.md)

LAV StarCraft II Host와 같은 LAN에 있는 사람 플레이어 PC에서 사용하는 최소 콘솔 프로토타입입니다.

이 프로젝트는 `LAV_v0.2`와 분리되어 있습니다. LAV를 실행하지 않고, `SC2AIApp` 또는 `Sc2LadderServer`를 수정하지 않습니다.

## 요구 사항

- Windows
- Python 3.14
- 사람 플레이어 PC에 설치된 StarCraft II
- 콘솔 도구는 Python 표준 라이브러리만 사용
- GUI 사용 시 선택적으로 Gradio 의존성 필요

## 초기 설정

```bat
cd /d C:\Vtuber_Souorce_Code\StarCraft2\LAV_SC2_HumanJoiner
py -3.14 -m venv .venv
.venv\Scripts\activate
python --version
```

## 기능

- UDP `47624` 포트에서 LAV StarCraft II LAN 로비 broadcast를 수신합니다.
- protocol version `1`의 `lav.sc2.lan_room` payload를 파싱합니다.
- `room_id`와 `source_id` 기준으로 현재 방 목록을 유지합니다.
- 방이 advertise한 TTL이 지나면 목록에서 제거합니다.
- 사람 PC에서 `SC2_x64.exe`를 찾습니다.
- 이후 실행에 사용할 command와 environment를 미리 보여줍니다.

콘솔 도구가 의도적으로 하지 않는 일:

- third-party package 설치
- `python-sc2` 또는 `burnysc2` 사용
- 마우스 또는 키보드 입력 자동화
- 실행 파일 패키징

Gradio GUI는 사용자가 `Join Game`을 눌렀을 때만 StarCraft II를 실행할 수 있습니다. 마우스 또는 키보드 입력 자동화는 하지 않습니다.

## 사용법

```bat
python human_joiner.py scan
```

scan 옵션:

```bat
python human_joiner.py scan --seconds 15 --port 47624
```

advertise된 host 또는 직접 입력한 host를 실행 전에 확인:

```bat
python human_joiner.py check --host 192.168.0.67 --proxy-ports 5677,5678
```

기본적으로 `check`와 `join`은 첫 번째 proxy port를 사람 플레이어 slot port로 사용합니다. 진단 목적일 때만 `--check-all-ports`를 사용하세요.

처음 발견된 방으로 StarCraft II 실행:

```bat
python human_joiner.py join --seconds 15
```

UDP discovery가 막혀 있지만 LAV Host IP를 알고 있을 때 StarCraft II 실행:

```bat
python human_joiner.py join --host 192.168.0.67 --proxy-ports 5677,5678
```

출력 예시:

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

StarCraft II를 찾지 못해도 joiner는 방 정보를 출력하고, 실행 파일 위치를 찾지 못했다고 알려줍니다.

## Gradio GUI

선택 GUI 의존성 설치:

```bat
.venv\Scripts\activate
python -m pip install -r requirements-gradio.txt
```

GUI 실행:

```bat
python human_joiner_gui.py
```

GUI 제공 기능:

- `Scan LAN`: LAV StarCraft II 방 찾기
- `Check Proxy`: advertise된 TCP proxy port 확인
- `Join Game`: 선택한 방 environment로 `SC2_x64.exe` 실행
- `Settings`: 로컬 `SC2_x64.exe` 경로 불러오기, 수정, 저장

브라우저 UI는 기본적으로 로컬 주소 `http://127.0.0.1:47860`에서 열립니다.

로컬 GUI 설정 저장 위치:

```text
config\human_joiner_config.json
```

예시:

```json
{
  "sc2_executable_path": "C:\\Program Files (x86)\\StarCraft II\\Versions\\Base97425\\SC2_x64.exe",
  "manual_host": "26.189.202.71",
  "manual_proxy_ports": "5677,5678",
  "manual_start_port": 5690,
  "manual_join_port": 47625
}
```

## LAN 로비 Payload

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

## 테스트

```bat
python -m unittest
```
