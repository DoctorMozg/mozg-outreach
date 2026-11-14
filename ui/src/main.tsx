import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ReactElement, StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Project } from "./pages/Project";
import { Projects } from "./pages/Projects";
import { EntityView, Registry } from "./pages/Registry";
import { ItemView } from "./pages/ItemView";
import { RunView } from "./pages/RunView";
import { Voice } from "./pages/Voice";
import { href, type Route, useRoute } from "./router";
import "./styles.css";

const client = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } });

function Page({ route }: { route: Route }): ReactElement {
  switch (route.page) {
    case "project":
      return <Project id={route.id} tab={route.tab} />;
    case "run":
      return <RunView id={route.id} />;
    case "item":
      return <ItemView id={route.id} />;
    case "voice":
      return <Voice />;
    case "registry":
      return <Registry />;
    case "entity":
      return <EntityView id={route.id} />;
    case "projects":
      return <Projects />;
  }
}

function App(): ReactElement {
  const route = useRoute();
  return (
    <>
      <header className="top">
        <a href={href.projects()} className="brand">
          mozg outreach
        </a>
        <nav>
          <a href={href.projects()}>Projects</a>
          <a href={href.registry()}>People and companies</a>
          <a href={href.voice()}>Voice</a>
        </nav>
        <span className="muted">drafts only, nothing is sent</span>
      </header>
      <main>
        <Page key={JSON.stringify(route)} route={route} />
      </main>
    </>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
