import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import PublicQuotePage from "./pages/PublicQuotePage";
import "./styles.css";
import "./login.css";
import ErrorBoundary from "./components/ErrorBoundary";
import { installErrorReporter } from "./errorReporter";

installErrorReporter();   // D-117

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {/* D-107: ответ логистической компании по ссылке — без входа и без оболочки CRM */}
    {window.location.pathname.startsWith("/q/") ? <PublicQuotePage token={decodeURIComponent(window.location.pathname.slice(3))} /> : <ErrorBoundary><App /></ErrorBoundary>}
  </React.StrictMode>,
);
