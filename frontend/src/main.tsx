import React from "react";
import ReactDOM from "react-dom/client";
// First: the boot overlay's minimal variant is painted from src/boot.ts and
// reads the app's theme custom properties, so the stylesheet must be in the
// document before that module evaluates.
import "./styles.css";
import "./boot";
import App from "./App";
import { I18nProvider } from "./i18n";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <I18nProvider>
      <App />
    </I18nProvider>
  </React.StrictMode>,
);
