from __future__ import annotations

import json
from pathlib import Path

from cleo.storage import GraphStore


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>{title}</title>
    <style>
      body {{
        font-family: "Inter", "Segoe UI", system-ui, sans-serif;
        margin: 0;
        background: #0f172a;
        color: #e2e8f0;
      }}
      header {{
        padding: 20px 32px;
        border-bottom: 1px solid #1e293b;
        display: flex;
        justify-content: space-between;
        align-items: center;
      }}
      header h1 {{
        font-size: 20px;
        margin: 0;
      }}
      header p {{
        margin: 4px 0 0;
        color: #94a3b8;
        font-size: 14px;
      }}
      #graph {{
        width: 100%;
        height: calc(100vh - 80px);
      }}
      .node circle {{
        stroke: #0f172a;
        stroke-width: 1.5px;
      }}
      .node text {{
        pointer-events: none;
        font-size: 12px;
      }}
      .link {{
        stroke: #475569;
        stroke-opacity: 0.8;
      }}
      .legend {{
        display: flex;
        gap: 12px;
        align-items: center;
      }}
      .legend span {{
        display: inline-flex;
        align-items: center;
        gap: 6px;
        font-size: 12px;
        color: #94a3b8;
      }}
      .legend i {{
        width: 10px;
        height: 10px;
        border-radius: 999px;
        display: inline-block;
      }}
    </style>
  </head>
  <body>
    <header>
      <div>
        <h1>{title}</h1>
        <p>{summary}</p>
      </div>
      <div class="legend">
        <span><i style="background:#38bdf8"></i>User</span>
        <span><i style="background:#f97316"></i>Video</span>
        <span><i style="background:#22c55e"></i>Channel</span>
        <span><i style="background:#a855f7"></i>Other</span>
      </div>
    </header>
    <svg id="graph"></svg>
    <script src="https://cdn.jsdelivr.net/npm/d3@7"></script>
    <script>
      const nodes = {nodes};
      const links = {links};

      const width = window.innerWidth;
      const height = window.innerHeight - 80;

      const svg = d3.select("#graph")
        .attr("viewBox", [0, 0, width, height]);

      const colorByType = (type) => {{
        switch (type) {{
          case "user":
            return "#38bdf8";
          case "video":
            return "#f97316";
          case "channel":
            return "#22c55e";
          default:
            return "#a855f7";
        }}
      }};

      const simulation = d3.forceSimulation(nodes)
        .force("link", d3.forceLink(links).id((d) => d.id).distance(140))
        .force("charge", d3.forceManyBody().strength(-320))
        .force("center", d3.forceCenter(width / 2, height / 2));

      const link = svg.append("g")
        .attr("stroke-width", 1.4)
        .selectAll("line")
        .data(links)
        .join("line")
        .attr("class", "link");

      const node = svg.append("g")
        .selectAll("g")
        .data(nodes)
        .join("g")
        .attr("class", "node")
        .call(
          d3.drag()
            .on("start", (event, d) => {{
              if (!event.active) simulation.alphaTarget(0.3).restart();
              d.fx = d.x;
              d.fy = d.y;
            }})
            .on("drag", (event, d) => {{
              d.fx = event.x;
              d.fy = event.y;
            }})
            .on("end", (event, d) => {{
              if (!event.active) simulation.alphaTarget(0);
              d.fx = null;
              d.fy = null;
            }})
        );

      node.append("circle")
        .attr("r", (d) => d.type === "user" ? 18 : d.type === "channel" ? 14 : 12)
        .attr("fill", (d) => colorByType(d.type));

      node.append("title").text((d) => d.label);

      node.append("text")
        .attr("x", 14)
        .attr("y", 4)
        .attr("fill", "#e2e8f0")
        .text((d) => d.label.length > 20 ? d.label.slice(0, 20) + "…" : d.label);

      simulation.on("tick", () => {{
        link
          .attr("x1", (d) => d.source.x)
          .attr("y1", (d) => d.source.y)
          .attr("x2", (d) => d.target.x)
          .attr("y2", (d) => d.target.y);

        node.attr("transform", (d) => `translate(${{d.x}},${{d.y}})`);
      }});
    </script>
  </body>
</html>
"""


def generate_html_report(db_path: str | Path, output_path: str | Path) -> Path:
    store = GraphStore(db_path)
    nodes = store.fetch_nodes()
    edges = store.fetch_edges()
    store.close()

    report_path = Path(output_path)
    node_payload = [
        {
            "id": node.node_id,
            "label": node.label,
            "type": node.node_type,
        }
        for node in nodes
    ]
    edge_payload = [
        {
            "source": edge.source_id,
            "target": edge.target_id,
            "type": edge.edge_type,
            "weight": edge.weight,
        }
        for edge in edges
    ]

    summary = f"Imported {len(nodes)} nodes and {len(edges)} edges."
    html = HTML_TEMPLATE.format(
        title="Cleo Graph Report",
        summary=summary,
        nodes=json.dumps(node_payload),
        links=json.dumps(edge_payload),
    )
    report_path.write_text(html, encoding="utf-8")
    return report_path
