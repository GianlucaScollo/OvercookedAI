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

> Dependency installation happens inside the Docker image build steps (Dockerfile(s) used by `docker-compose`).

### Required on the host (LLM / chat features)
You must have **Ollama installed and running on your host machine** (outside Docker), because the system connects to it at runtime. So, install **Ollama** (https://ollama.com/) and pull the required models.

Model identifiers are configured in `kitchen.mas2j` (Symbolic agent configuration):

- `gen_model = "qwen2.5-coder"`
- `emb_model = "all-minilm"`

Ollama setup:
```bash
ollama pull qwen2.5-coder
ollama pull all-minilm
```
Make sure Ollama is running (e.g., run `ollama serve` if it’s not already running).

> If your installed model names include tags (e.g. `:latest`, `:7b`, …), update `kitchen.mas2j` accordingly.

---

## Setup & Installation

1. **Clone this repository**
   ```bash
   git clone https://github.com/GianlucaScollo/OvercookedAI.git
   cd src/overcooked_demo
   ```

2. **Build the Docker images and start the Flask server**
   ```bash
   ./up.sh
   ```

3. **Wait until the Docker images are built and the server is running.**

4. **Stop the containers when you are done**
   ```bash
   ./down.sh
   ```

---

## Usage

Open your browser at: http://localhost

> When the match starts (or when the chat phase is triggered), the symbolic agent is started automatically.

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

- **Chat/LLM features not working**
  - Verify Ollama is running on the host.
  - Verify the models configured in `kitchen.mas2j` exist in your local Ollama installation.
  - Verify the container can reach Ollama at `http://host.docker.internal:11434` (or via `OLLAMA_HOST`).
- **Agent does not start**
  - Verify `SYMBOLIC_AGENT_DIR` points to the actual location of `SymbolicAIAgent` inside the container.
  - Rebuild the Docker images (`./down.sh && ./up.sh prod`)

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
