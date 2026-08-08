import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import "@xyflow/react/dist/style.css";
import "./styles/tokens.css";
import "./styles/app.css";
import "./styles/graph.css";

const root = document.getElementById("root");
if (root === null) {
  throw new Error("Nexolith UI root element is missing.");
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
