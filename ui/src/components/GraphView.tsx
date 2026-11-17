import { Box, Text } from "@mantine/core";
import { IconTopologyStar3 } from "@tabler/icons-react";
import ForceGraph3D, { type ConfigOptions, type ForceGraph3DInstance, type LinkObject, type NodeObject } from "3d-force-graph";
import { type ReactElement, useEffect, useRef } from "react";
import { BoxGeometry, Group, Mesh, MeshLambertMaterial, type PerspectiveCamera, SphereGeometry } from "three";
import type { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DObject, CSS2DRenderer } from "three/addons/renderers/CSS2DRenderer.js";
import type { Graph } from "../api";
import { href } from "../router";
import { Empty } from "./common";

const COLORS = { person: "#2f7d6d", company: "#3a5fa8", lead: "#b8742a" } as const;
// Mantine gray.6 reads on both the light and the dark body, so the WebGL links need no theme switch.
const LINK_COLOR = "#868e96";
const FOCUS_COLOR = "#e8590c";
const LABEL_HEIGHT = 16;
const MIN_CAMERA_DISTANCE = 160;

type GraphNode = NodeObject &
  Graph["nodes"][number] & {
    degree: number;
    radius: number;
    neighbours: Set<string>;
    labelElement?: HTMLDivElement;
    labelWidth?: number;
  };
type GraphLink = LinkObject<GraphNode> & { label: string };
type Instance = ForceGraph3DInstance<GraphNode, GraphLink>;
interface Rect {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

// The package types its constructor without node and link generics.
const Graph3D = ForceGraph3D as unknown as new (element: HTMLElement, config?: ConfigOptions) => Instance;

// `fill` stretches the graph over the rest of a flex column instead of a fixed-height box.
export function GraphView({ graph, fill = false }: { graph: Graph; fill?: boolean }): ReactElement {
  if (graph.nodes.length === 0) {
    return (
      <Empty icon={<IconTopologyStar3 size={32} />} title="Nothing linked yet">
        Enrich a lead to fill the registry.
      </Empty>
    );
  }
  return (
    <Box
      pos="relative"
      h={fill ? undefined : 520}
      bg="var(--mantine-color-body)"
      bd="1px solid var(--mantine-color-default-border)"
      style={{ borderRadius: "var(--mantine-radius-md)", overflow: "hidden", ...(fill && { flex: 1, minHeight: 400 }) }}
    >
      <Scene graph={graph} />
      <Text size="xs" c="dimmed" pos="absolute" left={12} bottom={8} style={{ pointerEvents: "none" }}>
        Drag a node to pin it, right-click to release. Drag the background to rotate, scroll to zoom.
      </Text>
    </Box>
  );
}

function Scene({ graph }: { graph: Graph }): ReactElement {
  const box = useRef<HTMLDivElement>(null);
  const fg = useRef<Instance | null>(null);
  // Frame the first tick at once, then ease to the layout once it settles.
  const framing = useRef<"initial" | "settling" | "done">("done");

  useEffect(() => {
    const element = box.current;
    if (!element) return;
    let hovered: GraphNode | null = null;
    let frame = 0;
    const scheduleLabels = (): void => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        placeLabels(instance, hovered);
      });
    };
    const touchesHovered = (link: GraphLink): boolean => hovered !== null && (link.source === hovered || link.target === hovered);
    const paintLinks = (): void => {
      // Fresh accessors make the library rebuild the link materials.
      instance
        .linkColor((link) => (touchesHovered(link) ? FOCUS_COLOR : LINK_COLOR))
        .linkWidth((link) => (touchesHovered(link) ? 1.4 : 0.5))
        .linkDirectionalArrowColor((link) => (touchesHovered(link) ? FOCUS_COLOR : LINK_COLOR));
    };

    const instance = new Graph3D(element, { controlType: "orbit", extraRenderers: [new CSS2DRenderer()] })
      .backgroundColor("rgba(0,0,0,0)")
      .showNavInfo(false)
      .nodeThreeObject(nodeObject)
      .linkLabel((link) => (link.label ? tooltip(link.label) : ""))
      .linkOpacity(0.6)
      .linkDirectionalArrowLength(3.5)
      .linkDirectionalArrowRelPos(1)
      .warmupTicks(80)
      .cooldownTime(6000)
      .onEngineTick(() => {
        if (framing.current === "initial") {
          framing.current = "settling";
          fitToNodes(instance, 0);
        }
        scheduleLabels();
      })
      .onEngineStop(() => {
        if (framing.current !== "settling") return;
        framing.current = "done";
        fitToNodes(instance, 800);
      })
      // While dragging, the library fixes only the dragged node and keeps the rest simulating.
      // Keeping it fixed after release is the Neo4j behavior.
      .onNodeDragEnd((node) => {
        node.fx = node.x;
        node.fy = node.y;
        node.fz = node.z;
      })
      .onNodeRightClick((node) => {
        node.fx = node.fy = node.fz = undefined;
        instance.d3ReheatSimulation();
      })
      .onNodeHover((node) => {
        hovered = node;
        element.style.cursor = node ? "pointer" : "";
        paintLinks();
        scheduleLabels();
      })
      .onNodeClick((node) => {
        const id = String(node.id);
        window.location.hash = id.startsWith("i") ? href.item(Number(id.slice(1))) : href.entity(Number(id.slice(1)));
      });
    paintLinks();
    instance.d3Force("charge")?.strength?.(-90);
    instance.d3Force("link")?.distance?.(40);
    // Without it, the many small disconnected clusters drift apart and the nodes shrink to dots.
    instance.d3Force("gravity", gravity(0.04));
    (instance.controls() as OrbitControls).addEventListener("change", scheduleLabels);
    fg.current = instance;

    const resize = new ResizeObserver(([entry]) => {
      if (!entry) return;
      instance.width(entry.contentRect.width).height(entry.contentRect.height);
      scheduleLabels();
    });
    resize.observe(element);
    return () => {
      cancelAnimationFrame(frame);
      resize.disconnect();
      instance._destructor();
      // Browsers cap live WebGL contexts, and page switches remount this view.
      instance.renderer().forceContextLoss();
      element.replaceChildren();
      fg.current = null;
    };
  }, []);

  useEffect(() => {
    const instance = fg.current;
    if (!instance) return;
    // A refetch keeps where nodes were and which ones the user pinned.
    const previous = new Map(instance.graphData().nodes.map((n) => [n.id, n]));
    const neighbours = new Map<string, Set<string>>(graph.nodes.map((n) => [n.id, new Set()]));
    for (const e of graph.edges) {
      neighbours.get(e.source)?.add(e.target);
      neighbours.get(e.target)?.add(e.source);
    }
    const nodes: GraphNode[] = graph.nodes.map((n) => {
      const old = previous.get(n.id);
      const linked = neighbours.get(n.id) ?? new Set<string>();
      return {
        ...n,
        degree: linked.size,
        // Hubs grow with their link count so the eye finds them first.
        radius: 5 + 2 * Math.sqrt(linked.size),
        neighbours: linked,
        ...(old && { x: old.x, y: old.y, z: old.z, fx: old.fx, fy: old.fy, fz: old.fz }),
      };
    });
    // A refetch keeps the user's view; the first load gets framed.
    framing.current = previous.size > 0 ? "done" : "initial";
    instance.graphData({ nodes, links: graph.edges.map((e) => ({ ...e })) });
  }, [graph]);

  return <div ref={box} style={{ position: "absolute", inset: 0 }} />;
}

function nodeObject(node: GraphNode): Group {
  const material = new MeshLambertMaterial({ color: COLORS[node.kind] });
  const side = node.radius * 1.6;
  const mesh =
    node.kind === "lead" ? new Mesh(new BoxGeometry(side, side, side), material) : new Mesh(new SphereGeometry(node.radius, 24, 16), material);
  const element = document.createElement("div");
  element.className = `graph-label graph-label-${node.kind} graph-label-hidden`;
  element.textContent = node.label;
  node.labelElement = element;
  const label = new CSS2DObject(element);
  label.center.set(0.5, 0);
  label.position.set(0, -(node.radius + 2), 0);
  const group = new Group();
  group.add(mesh, label);
  return group;
}

// zoomToFit frames the bounding box, whose corners leave a round 3D cluster at half the view.
function fitToNodes(instance: Instance, durationMs: number): void {
  const nodes = instance.graphData().nodes;
  if (nodes.length === 0) return;
  const axis = (pick: (n: GraphNode) => number): number => (Math.min(...nodes.map(pick)) + Math.max(...nodes.map(pick))) / 2;
  const center = { x: axis((n) => n.x ?? 0), y: axis((n) => n.y ?? 0), z: axis((n) => n.z ?? 0) };
  const radius = Math.max(...nodes.map((n) => Math.hypot((n.x ?? 0) - center.x, (n.y ?? 0) - center.y, (n.z ?? 0) - center.z) + n.radius));
  const camera = instance.camera() as PerspectiveCamera;
  const vertical = (camera.fov * Math.PI) / 360;
  const horizontal = Math.atan(Math.tan(vertical) * camera.aspect);
  // Leave room for the labels under the outermost nodes; the floor keeps a handful of nodes from filling the box.
  const distance = Math.max(MIN_CAMERA_DISTANCE, (radius * 1.08) / Math.sin(Math.min(vertical, horizontal)));
  instance.cameraPosition({ x: center.x, y: center.y, z: center.z + distance }, center, durationMs);
}

// Map-style culling: labels claim screen space by priority, and one that would overlap a placed label hides.
function placeLabels(instance: Instance, hovered: GraphNode | null): void {
  const camera = instance.camera().position;
  // 0 is the hovered node, 1 its neighbours, 2 the rest.
  const rank = (n: GraphNode): number => (n === hovered ? 0 : hovered?.neighbours.has(String(n.id)) ? 1 : 2);
  const distance = (n: GraphNode): number => Math.hypot((n.x ?? 0) - camera.x, (n.y ?? 0) - camera.y, (n.z ?? 0) - camera.z);
  const nodes = instance
    .graphData()
    .nodes.map((node) => ({ node, rank: rank(node), distance: distance(node) }))
    .sort((a, b) => a.rank - b.rank || b.node.degree - a.node.degree || a.distance - b.distance);
  const placed: Rect[] = [];
  for (const { node, rank } of nodes) {
    const element = node.labelElement;
    if (!element) continue;
    element.classList.toggle("graph-label-focus", rank < 2);
    element.classList.toggle("graph-label-hovered", rank === 0);
    // CSS2DRenderer hides labels outside the camera frustum.
    if (element.style.display === "none") continue;
    // The hovered label drops its ellipsis, so it is measured afresh.
    const width = rank === 0 ? element.offsetWidth : (node.labelWidth ||= element.offsetWidth);
    const anchor = instance.graph2ScreenCoords(node.x ?? 0, (node.y ?? 0) - node.radius - 2, node.z ?? 0);
    const rect = { left: anchor.x - width / 2, top: anchor.y, right: anchor.x + width / 2, bottom: anchor.y + LABEL_HEIGHT };
    const free = rank === 0 || !placed.some((p) => rect.left < p.right && rect.right > p.left && rect.top < p.bottom && rect.bottom > p.top);
    element.classList.toggle("graph-label-hidden", !free);
    if (free) placed.push(rect);
  }
}

// A weak pull toward the origin, like d3's forceX/Y/Z with a zero target.
function gravity(strength: number): ((alpha: number) => void) & { initialize: (nodes: GraphNode[]) => void } {
  let nodes: GraphNode[] = [];
  const force = (alpha: number): void => {
    const k = strength * alpha;
    for (const n of nodes) {
      n.vx = (n.vx ?? 0) - (n.x ?? 0) * k;
      n.vy = (n.vy ?? 0) - (n.y ?? 0) * k;
      n.vz = (n.vz ?? 0) - (n.z ?? 0) * k;
    }
  };
  force.initialize = (initial: GraphNode[]): void => {
    nodes = initial;
  };
  return force;
}

// Labels come from scraped pages, so the tooltip gets a text node, never HTML.
function tooltip(text: string): HTMLElement {
  const element = document.createElement("span");
  element.textContent = text;
  return element;
}
