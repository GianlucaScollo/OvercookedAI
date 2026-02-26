# Design and Implementation of Human–AI Cooperation in a Video Game

**Author:** Margarita H. Radeva & Gianluca Scollo

---

## Overview  
This repository contains the **Overcooked-AI** benchmark environment in which a human player can team up with a Jason symbolic AI agent to prepare and serve soups under time pressure. It provides the game server, frontend UI, and Docker configurations needed to launch the kitchen simulation.

> **Note:** This is one of **two** repositories required for the complete system. You will also need the [Symbolic AI Agent repo](https://github.com/GianlucaScollo/SymbolicAIAgent.git).

---

## Links & Related Repos

- **Symbolic AI Agent (Jason)**  
  https://github.com/GianlucaScollo/SymbolicAIAgent.git  
- **Original Overcooked-AI (core environment):**
  https://github.com/margaritaradeva/OvercookedAI.git
- **Original Overcooked-AI repository**  
  https://github.com/HumanCompatibleAI/overcooked_ai  

---

## Prerequisites

Make sure you have installed on your system:

- **Python** 3.8 or higher  
- **Docker**
- **Git Bash** (set as your default terminal, e.g. in VS Code, if using Windows)
- A web browser (Chrome, Firefox, Edge)  

> All of the additional requirements are installed via Docker itself

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
3. **Wait until you see a message like this in the terminal:**
   ```bash
   Running on ......... (Press CTRL+C to quit)
   ```
4. **When you are done, stop the container**
   ```bash
   ./down.sh
   ```

---

## Usage
1. Open your browser at http://localhost
2. Select a **kitchen layout** and **game time**, then click **Create**.
3. In a separate terminal, follow the **Symbolic AI Agent** README to launch the Jason agent (gradle run).
4. The agent will join automatically as Player 2. Play together and observe real-time human-AI coordination!

---

## Repository Structure
```plaintext
OvercookedAI/
└── src/
    └── overcooked_demo/
        ├── server/
        |  ├── app.py             # Flask server and Socket.IO enrypoint
        |  ├── config.json        # Global settings and layouts
        |  ├── Dockerfile         # Flask container definition
        |  ├── game.py            # Core game logic (wraps Overcooked MDP)
        |  ├── requirements.txt   # Dependencies
        |  ├── utils.py           # Helper functions
        |  ├── graphics/          # Overcooked graphics (JS)
        |  └── static/            # Frontend assets (HTML, JS, CSS, images)
        |      ├── assets/        # Contains layout sprites
        │      ├── css/           
        │      ├── images/        # Images used on instructions.html adn tutorial.html
        │      ├── js/
        |      └── templates/     # HTML templates: index, instructions, tutorial
        |  
        ├── docker-compose.yml 
        ├── up.sh                 # Build and start Docker containers
        ├── down.sh               # Stop and remove containers
        └── README.md             # You are here now!
```

---

## Acknowledgements

- **HumanCompatibleAI/overcooked_ai** for the original cooperative benchmark environment.

---