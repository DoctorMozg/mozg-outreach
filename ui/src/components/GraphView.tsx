import cytoscape from "cytoscape";
import { type ReactElement, useEffect, useRef } from "react";
import type { Graph } from "../api";
import { href } from "../router";

const COLORS = { person: "#2f7d6d", company: "#3a5fa8", lead: "#b8742a" };

export function GraphView({ graph }: { graph: Graph }): ReactElement {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!box.current) return;
    const cy = cytoscape({
      container: box.current,
      elements: [
        ...graph.nodes.map((n) => ({ data: { id: n.id, label: n.label, kind: n.kind } })),
        ...graph.edges.map((e, n) => ({ data: { id: `edge${n}`, source: e.source, target: e.target, label: e.label } })),
      ],
      style: [
        {
          selector: "node",
          style: {
            label: "data(label)",
            "font-size": 10,
            width: 24,
            height: 24,
            "text-wrap": "ellipsis",
            "text-max-width": "120px",
            color: "#444",
            "background-color": (node: cytoscape.NodeSingular) => COLORS[node.data("kind") as keyof typeof COLORS],
          },
        },
        { selector: "node[kind = 'lead']", style: { shape: "round-rectangle" } },
        { selector: "edge", style: { label: "data(label)", "font-size": 8, width: 1, "curve-style": "bezier", "target-arrow-shape": "triangle" } },
      ],
      layout: { name: "cose", animate: false },
      // A handful of nodes would otherwise be zoomed to fill the whole box.
      maxZoom: 1.5,
    });
    // Clicking a node opens its profile or lead.
    cy.on("tap", "node", (event) => {
      const id = String(event.target.id());
      window.location.hash = id.startsWith("i") ? href.item(Number(id.slice(1))) : href.entity(Number(id.slice(1)));
    });
    return () => cy.destroy();
  }, [graph]);
  if (graph.nodes.length === 0) return <p className="hint">Nothing linked yet. Enrich a lead to fill the registry.</p>;
  return <div ref={box} className="graph" />;
}
