import { useEffect, useState } from "react";

// Hash routes keep deep links working when FastAPI serves the built files statically.
export type Route =
  | { page: "projects" }
  | { page: "project"; id: number; tab: string }
  | { page: "run"; id: number }
  | { page: "item"; id: number }
  | { page: "voice" }
  | { page: "registry" }
  | { page: "entity"; id: number };

export function parse(hash: string): Route {
  const [page, id, tab] = hash.replace(/^#\/?/, "").split("/");
  const num = Number(id);
  switch (page) {
    case "project":
      return { page: "project", id: num, tab: tab ?? "brief" };
    case "run":
    case "item":
    case "entity":
      return { page, id: num };
    case "voice":
    case "registry":
      return { page };
    default:
      return { page: "projects" };
  }
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parse(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parse(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

export const href = {
  projects: () => "#/",
  project: (id: number, tab = "brief") => `#/project/${id}/${tab}`,
  run: (id: number) => `#/run/${id}`,
  item: (id: number) => `#/item/${id}`,
  voice: () => "#/voice",
  registry: () => "#/registry",
  entity: (id: number) => `#/entity/${id}`,
};
