# Design and Implementation of Human–AI Cooperation in a Video Game

**Author:** Margarita H. Radeva and Gianluca Scollo

---

## Overview  
This repository contains the **Overcooked-AI** benchmark environment in which a human player can team up with a Jason symbolic AI agent to prepare and serve soups under time pressure. It provides the game server, frontend UI, and Docker configurations needed to launch the kitchen simulation.

> **Single entrypoint:** the execution of the full system is centralized in this repository.  
> The symbolic teammate (repository `SymbolicAIAgent`) is automatically fetched and started from here.

---

## Links & Related Repos

- **Symbolic AI Agent (Jason + ChatBDI integration)**  
  https://github.com/GianlucaScollo/SymbolicAIAgent  
- **ChatBDI (reference for Ollama model configuration)**  
  https://github.com/VEsNA-ToolKit/chatbdi  
- **Original Overcooked-AI fork used as base (reference)**  
  https://github.com/margaritaradeva/OvercookedAI  
- **Original Overcooked-AI repository**  
  https://github.com/HumanCompatibleAI/overcooked_ai  

---

## Prerequisites

### Required (always)
- **Docker**
- **Git Bash** (set as your default terminal, e.g. in VS Code, if using Windows)
- A web browser (Chrome, Firefox, Edge)

> **No host dependencies required:** You do NOT need to install Ollama or Python on your physical machine. Everything, including the LLM backend, is fully containerized and will be downloaded automatically by Docker.

---

## Setup & Installation

1. **Clone this repository**
   ```bash
   git clone https://github.com/GianlucaScollo/OvercookedAI.git
   cd src/overcooked_demo
   ```

2. **Build the Docker images and start the system**
   ```bash
   ./up.sh
   ```

3. **FIRST RUN ONLY: Wait for the LLM models to download!**  
   Upon the first execution, the `ollama` Docker container will silently download the AI models (`qwen2.5-coder` and `all-minilm`) in the background. Since these files are several gigabytes in size, **this process might take several minutes** depending on your internet connection. 
   
   To check the download progress in real-time, run:
   ```bash
   docker-compose logs -f ollama
   ```
   *When you see `success` for both models in the logs, the environment is fully ready.*

4. **Stop the containers when you are done**
   ```bash
   ./down.sh
   ```
   *Note: Downloaded models are safely stored in a Docker volume and will NOT be re-downloaded on future runs.*

---

## Usage

Open your browser at: http://localhost

> When the match starts (or when the chat phase is triggered), the symbolic agent is started automatically.

---

## Changing AI Models

If you want to change the LLM models used by the agent, you must update the configuration in **two different places**:

1. **Update the Docker environment:**  
   Open `src/overcooked_demo/docker-compose.yml` and change the models in the `entrypoint` script of the `ollama` service:
   ```yaml
   ollama pull <new-generation-model> && 
   ollama pull <new-embedding-model> && 
   ```

2. **Update the Agent Configuration:**  
   In your fork of the `SymbolicAIAgent` repository, modify the `kitchen.mas2j` file:
   ```plaintext
   gen_model = "<new-generation-model>",
   emb_model = "<new-embedding-model>"
   ```

Once both are updated, run `./down.sh` followed by `./up.sh` to apply the changes.

#### Note about changing models / parameters for SymbolicAIAgent
This repo automatically fetches the agent repository at Docker build time (via `git clone` in `src/overcooked_demo/server/Dockerfile`).
Therefore, if you change agent parameters (e.g. `kitchen.mas2j`), you typically:
- apply the change in **your own fork / modified copy** of `SymbolicAIAgent` (commit + push there), then
- update the `git clone` URL in the Dockerfile to point to your fork (or otherwise ensure it clones the modified repository), and/or set the correct `AGENT_BRANCH`,
- rebuild/restart the containers so the updated agent code is fetched and used.

Example (Dockerfile):
```dockerfile
RUN git clone https://github.com/<your-username>/SymbolicAIAgent.git --branch $AGENT_BRANCH --single-branch /app/SymbolicAIAgent
```

---

## Changing the experiment flow (route) and the game map

The demo/experiment flow is controlled by:
1) an environment variable `ROUTE` in `src/overcooked_demo/docker-compose.yml`, and  
2) the `ROUTE_FLOWS` configuration in `src/overcooked_demo/server/app.py`.

> Note: in the backend code and URLs, the route value is often referred to as `order` (e.g., `order=route1`). It corresponds to the `ROUTE` environment variable.

### 1) Select which route flow to run (ROUTE)

In `src/overcooked_demo/docker-compose.yml` you can choose the flow variant by setting `ROUTE`:

```yaml
environment:
  # ...
  ROUTE: "route1"  # or "route2" <- flag to select flow variant (route1, route2, etc.)
```

The Flask server reads it at startup (default: `route1`) and uses it as the initial route:

```python
ROUTE = os.getenv('ROUTE', 'route1')
```

### 2) Change the sequence of steps (ROUTE_FLOWS)

In `src/overcooked_demo/server/app.py`, the `ROUTE_FLOWS` dictionary defines the steps for each route (pages, chat phases, games).

A game step looks like:

```python
{'type': 'game', 'layout': 'cramped_room', 'time': 60},
```

You can edit:
- `layout`: the map/layout name
- `time`: match duration in seconds

### 3) Available layouts (maps)

Valid layout names are listed in `src/overcooked_demo/server/config.json` under the `layouts` field, for example:

```json
"layouts": ["cramped_new", "cramped_room", "asymmetric_advantages", "coordination_ring", "forced_coordination", "counter_circuit"]
```

Choose one of those strings and set it as the `layout` value in the desired `{'type': 'game', ...}` step.

> Note (VS Code / saving): after editing `docker-compose.yml`, `app.py`, or `config.json`, make sure the files are saved (e.g., `Ctrl+S`) before restarting containers.
> If you tend to forget saving changes, you can enable Auto Save in VS Code (File → Auto Save).
> After changing `ROUTE`, `ROUTE_FLOWS`, or the configuration, restart/rebuild the containers as needed (e.g. `./down.sh && ./up.sh`) so the running server uses the updated settings.

---

## How the symbolic agent is started (architecture)

### 1) Frontend trigger
In the frontend templates, the UI triggers an event such as:
```js
socket.emit('agent:start', { context: 'chat-start' });
```

### 2) Backend handler
The backend receives the Socket.IO event in `app.py` and calls:
- `start_symbolic_agent()`

### 3) Agent location inside the container
The backend resolves the agent directory using:

- environment variable: `SYMBOLIC_AGENT_DIR`
- default value: `"/app/SymbolicAIAgent"`

Example:
```python
_AGENT_DIR = os.environ.get("SYMBOLIC_AGENT_DIR", "/app/SymbolicAIAgent")
```

The agent repository is placed inside the container so it can be started when needed.

---

## Troubleshooting

- **Agent does not respond or chat fails on first launch:**
  - The Ollama models might still be downloading in the background. Check the download progress using `docker-compose logs -f ollama`.
  - Ensure you haven't manually stopped the `ollama` container.
- **Agent does not start:**
  - Verify `SYMBOLIC_AGENT_DIR` points to the actual location of `SymbolicAIAgent` inside the container.
  - Rebuild the Docker images (`./down.sh && ./up.sh`)
- **Port conflicts:**
  - If the `docker-compose up` command fails with a port conflict error, ensure you don't have another web server running on port 80 or 5000, and optionally check if a local installation of Ollama is interfering on port 11434.

---

## Repository Structure

```plaintext
OvercookedAI/
├── src/overcooked_demo/
|   ├── server/
|   |   ├── graphics/            # Overcooked graphics (JS)
|   |   ├── static/              # Frontend assets (HTML, JS, CSS, images, etc.)
|   |   |   ├── assets/          # Contains layout sprites
|   |   |   ├── css/
|   |   |   ├── images/          # Images used on instructions.html and tutorial.html
|   |   |   ├── js/
|   |   |   ├── json/            # Configuration files used on chat_room.html and survey.html
|   |   |   ├── lib/
|   |   |   └── templates/       # HTML templates: index, instructions, tutorial
|   |   ├── Dockerfile           # Flask container definition
|   |   ├── app.py               # Flask server and Socket.IO entrypoint
|   |   ├── config.json          # Global settings and layouts
|   |   ├── game.py              # Core game logic (wraps Overcooked MDP)
|   |   ├── requirements.txt     # Dependencies
|   |   └── utils.py             # Helper functions
|   ├── docker-compose.yml       # Docker configuration files
|   ├── down.sh                  # Stop and remove containers
|   └── up.sh                    # Build and start Docker containers
└── README.md                    # You are here now!

```

---

## Acknowledgements

- **HumanCompatibleAI/overcooked_ai** for the original cooperative benchmark environment.
- **VEsNA-ToolKit/chatbdi** for the ChatBDI framework integrated into this version.

---
