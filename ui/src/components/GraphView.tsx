import { Box, useComputedColorScheme } from "@mantine/core";
import { IconTopologyStar3 } from "@tabler/icons-react";
import cytoscape from "cytoscape";
import { type ReactElement, useEffect, useRef } from "react";
import type { Graph } from "../api";
import { href } from "../router";
import { Empty } from "./common";

const COLORS = { person: "#2f7d6d", company: "#3a5fa8", lead: "#b8742a" };

export function GraphView({ graph }: { graph: Graph }): ReactElement {
  const box = useRef<HTMLDivElement>(null);
  // Cytoscape paints on a canvas, so the label color can't come from CSS variables.
  const labelColor = useComputedColorScheme("light") === "dark" ? "#c9c9c9" : "#444";
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
            color: labelColor,
            "background-color": (node: cytoscape.NodeSingular) => COLORS[node.data("kind") as keyof typeof COLORS],
          },
        },
        { selector: "node[kind = 'lead']", style: { shape: "round-rectangle" } },
        { selector: "edge", style: { label: "data(label)", color: labelColor, "font-size": 8, width: 1, "curve-style": "bezier", "target-arrow-shape": "triangle" } },
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
  }, [graph, labelColor]);
  if (graph.nodes.length === 0) {
    return (
      <Empty icon={<IconTopologyStar3 size={32} />} title="Nothing linked yet">
        Enrich a lead to fill the registry.
      </Empty>
    );
  }
  return <Box ref={box} h={520} bd="1px solid var(--mantine-color-default-border)" style={{ borderRadius: "var(--mantine-radius-md)" }} />;
}
