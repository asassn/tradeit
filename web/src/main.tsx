import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createHashRouter, Navigate, RouterProvider } from "react-router-dom";
import { Shell } from "@/app/Shell";
import { DecisionsView } from "@/views/DecisionsView";
import { PlanView } from "@/views/PlanView";
import { HorizonsView } from "@/views/HorizonsView";
import { CaptureHealthView } from "@/views/CaptureHealthView";
import { NotYetView } from "@/views/NotYetView";
import { RunsView } from "@/views/RunsView";
import { WalkForwardView } from "@/views/WalkForwardView";
import { SecuritiesView } from "@/views/SecuritiesView";
import "@/tokens/tokens.css";
import "@/tokens/base.css";

/**
 * A HASH router, deliberately.
 *
 * The bundle is served by FastAPI from a static mount with no catch-all
 * rewrite, so a browser-history route would 404 on a hard refresh or a pasted
 * link. A hash route needs no server cooperation at all, which keeps the
 * deployment story "FastAPI serves a directory" rather than "FastAPI serves a
 * directory and also knows every client route".
 */
const router = createHashRouter([
  {
    path: "/",
    element: <Shell />,
    children: [
      { index: true, element: <Navigate to="/plan" replace /> },
      { path: "plan", element: <PlanView /> },
      { path: "securities", element: <SecuritiesView /> },
      { path: "securities/:securityId", element: <SecuritiesView /> },
      { path: "evidence/runs", element: <RunsView /> },
      { path: "evidence/walkforward", element: <WalkForwardView /> },
      { path: "evidence/horizons", element: <HorizonsView /> },
      { path: "decisions", element: <DecisionsView /> },
      { path: "system/capture", element: <CaptureHealthView /> },
      { path: "not-yet", element: <NotYetView /> },
      { path: "*", element: <Navigate to="/plan" replace /> },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
