import "@mantine/core/styles.css";
import "@mantine/notifications/styles.css";
import "./legacy.css";
import "./app.css";
import { MantineProvider } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ReactElement, StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Shell } from "./components/Shell";
import { ItemView } from "./pages/ItemView";
import { Project } from "./pages/Project";
import { Projects } from "./pages/Projects";
import { EntityView, Registry } from "./pages/Registry";
import { RunView } from "./pages/RunView";
import { Voice } from "./pages/Voice";
import { type Route, useRoute } from "./router";
import { theme } from "./theme";

const client = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } });

function Page({ route }: { route: Route }): ReactElement {
  switch (route.page) {
    case "project":
      return (
        <div className="legacy">
          <Project id={route.id} tab={route.tab} />
        </div>
      );
    case "run":
      return (
        <div className="legacy">
          <RunView id={route.id} />
        </div>
      );
    case "item":
      return (
        <div className="legacy">
          <ItemView id={route.id} />
        </div>
      );
    case "voice":
      return (
        <div className="legacy">
          <Voice />
        </div>
      );
    case "registry":
      return (
        <div className="legacy">
          <Registry />
        </div>
      );
    case "entity":
      return (
        <div className="legacy">
          <EntityView id={route.id} />
        </div>
      );
    case "projects":
      return <Projects />;
  }
}

function App(): ReactElement {
  const route = useRoute();
  return (
    <Shell route={route}>
      <Page key={JSON.stringify(route)} route={route} />
    </Shell>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <MantineProvider theme={theme} defaultColorScheme="auto">
      <Notifications position="top-right" />
      <QueryClientProvider client={client}>
        <App />
      </QueryClientProvider>
    </MantineProvider>
  </StrictMode>,
);
