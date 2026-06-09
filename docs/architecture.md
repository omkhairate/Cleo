# Cleo Architecture

## Product Goal

Build one personal AI assistant that feels continuous across all surfaces:

- terminal
- app UI
- browser companion
- future automations or messaging channels

The key design rule is simple:

> one assistant brain, many clients

## Core Principles

### 1. Centralized assistant backend

The backend should own:

- conversation state
- memory
- tool calling
- app credentials
- connector discovery
- scheduling
- auth and permissions

This avoids building separate "mini assistants" per surface.

### 2. Thin clients

Clients should mostly do:

- sign in
- render messages
- stream responses
- capture voice/text/images
- invoke assistant actions

Business logic stays server-side.

### 3. Connector-first design

Every external app should be wrapped behind a common interface:

- identity
- auth status
- capabilities
- actions
- sync hooks

This lets us add Gmail, Notion, Slack, GitHub, Google Calendar, local files, and others without changing the assistant contract.

### 4. Memory as a system, not a chat log

Use layered memory:

- short-term conversation context
- user profile and preferences
- durable facts
- app-derived context
- task state

Also represent memory as a graph:

- nodes for people, projects, goals, docs, app objects, tasks, and concepts
- edges for relationships like `owns`, `mentions`, `depends_on`, `belongs_to`, and `last_seen_in`
- graph traversal for context retrieval, not just vector similarity

This is the same high-level value shown in Cognee's graph-based retrieval example: linked context creates a usable "memory map" instead of isolated chunks ([Cognee article](https://www.cognee.ai/blog/fundamentals/cognee-links-documents)).

### 5. Ubiquity through APIs

If the assistant is available through HTTP and streaming endpoints, every client becomes easy to build:

- CLI
- web app
- mobile app
- messaging bots
- automations

## Suggested Stack

### Backend

- FastAPI
- Pydantic
- Postgres for durable data
- Redis for queues/caching if needed
- background worker for scheduled jobs

### Assistant core

- tool/router layer
- connector registry
- memory service
- brain graph service
- provider abstraction for LLMs

### Initial model choice

For the first local runtime, use [HuggingFaceTB/SmolLM3-3B](https://huggingface.co/HuggingFaceTB/SmolLM3-3B).

Why this is the current default:

- smaller footprint than `Qwen3-8B`
- more comfortable on a `16 GB` M1 Mac while other apps are running
- still aimed at reasoning and tool-use style assistant tasks

Recommended posture:

- use `SmolLM3-3B` as the always-on local assistant model
- serve it locally through Ollama first for the lowest integration friction
- keep the provider interface flexible so we can upgrade to a larger model later for heavier tasks

### Clients

- CLI in Python or Node
- native macOS overlay for fast summon-and-dismiss desktop access
- mobile app in React Native / Expo
- browser companion UI for setup, approvals, and desktop chat
- optional Telegram / WhatsApp / iMessage bridge later

## Access Strategy

### Terminal

Use the CLI for:

- quick chat
- agent commands
- summaries
- app actions
- scripts and cron jobs

### Desktop Overlay

Use the native desktop overlay for:

- Spotlight-style instant access
- quick commands without context switching
- short chat and command execution
- future global actions across apps

### App UI

Use the app UI as the primary day-to-day surface:

- chat
- tasks
- integrations
- memory settings
- approvals
- notifications

### Browser Companion

Use the browser as a secondary surface for:

- onboarding
- settings
- long-form history
- connector management
- desktop chat

### Phone

Best order if app-first is the product goal:

1. React Native / Expo app
2. push notifications and background task hooks
3. optional messaging bridge for ultra-fast access

This gives "access from anywhere" while still feeling like a real product, not just a website.

## Connector Strategy

Split connectors into three classes:

### 1. OAuth SaaS connectors

Examples:

- Google
- Notion
- Slack
- GitHub
- Linear

### 2. Local/system connectors

Examples:

- filesystem
- terminal commands
- local databases
- notes folders

### 3. Protocol connectors

Examples:

- MCP servers
- webhooks
- IMAP/SMTP
- CalDAV/CardDAV

## Brain Graph

The assistant should expose a graph view of its internal memory model.

### Node types

- user
- contact
- project
- task
- document
- memory
- connector
- app object
- concept
- surface

### Edge types

- connected_to
- mentions
- related_to
- depends_on
- owns
- synced_from
- served_on
- uses

### Visualization goals

- show what the assistant knows
- show how knowledge is connected
- show where a fact came from
- let the user inspect clusters by app, project, or person
- eventually support clicking a node to open source context

### MVP implementation

- API endpoint that returns `nodes` and `edges`
- app screen that renders the graph
- seeded graph data from memory + configured connectors

### Future implementation

- graph persistence in Postgres or Neo4j
- graph enrichment from ingestion pipelines
- hybrid retrieval using vector search plus graph traversal
- user controls for pruning, pinning, and labeling memories

## Security Model

This matters a lot for a personal assistant with broad access.

Minimum rules:

- every connector has explicit scopes
- destructive actions require confirmation
- secrets live in environment or secret storage, not source
- audit log every tool/action call
- keep personal and work accounts separable

## Delivery Plan

### Phase 1: working spine

- assistant API
- CLI
- local memory
- connector registry
- `SmolLM3-3B` local runtime integration

### Phase 2: useful assistant

- auth
- persistent storage
- first 3 high-value connectors
- task memory
- background jobs

### Phase 3: everywhere access

- mobile app
- browser companion
- notifications
- voice input
- messaging bridge

### Phase 4: proactive assistant

- reminders
- inbox triage
- meeting prep
- daily briefings
- cross-app workflows

## What To Build First

If the goal is "all-purpose" without getting stuck, start with these:

1. conversation API
2. connector registry
3. user/profile memory
4. Google connector
5. Notion connector
6. GitHub connector
7. mobile app
8. browser companion

That gives broad leverage fast.
