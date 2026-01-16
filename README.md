# Cleo

## Data ingestion + visualization overview

### Supported data sources (planned)
- YouTube API
- Instagram data export/API
- Google Takeout history
- Reddit history

### Ingestion approach
- OAuth for API-based sources (YouTube, Instagram, Reddit where available).
- Export uploads for offline archives (Google Takeout, Instagram exports).
- Incremental sync for connected accounts to keep data fresh.

### Storage model
- Nodes and edges stored in either:
  - A graph database (native nodes/edges), or
  - Relational tables that model nodes and edges (e.g., `nodes`, `edges`, `node_properties`).

### Visualization approach
- Frontend-rendered graph using D3 + React, or
- Backend-generated graph (precomputed layout) served to the UI.

### Data flow (intended pipeline)
1. **Auth / Upload** → OAuth connect or export upload.
2. **Ingest** → Normalize source data into a common schema.
3. **Store** → Persist nodes/edges in graph DB or relational tables.
4. **Sync** → Incrementally refresh connected sources.
5. **Visualize** → Render graph in UI or stream precomputed layout.
