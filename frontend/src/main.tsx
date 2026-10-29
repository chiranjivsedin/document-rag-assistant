import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "@fontsource-variable/inter"; // bundled with the app, so no request goes to a font CDN
import { App } from "./App";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
