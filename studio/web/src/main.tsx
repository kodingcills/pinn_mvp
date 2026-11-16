import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, NavLink, Route, Routes } from "react-router";
import { Runs } from "./pages/Runs";
import { Replay } from "./pages/Replay";
import { Compare } from "./pages/Compare";
import { Launch } from "./pages/Launch";
import { Evidence } from "./pages/Evidence";
import "./styles.css";

function App() {
  return (
    <BrowserRouter>
      <nav className="top">
        <span className="brand">Haplous Studio</span>
        <NavLink to="/" end>Runs</NavLink>
        <NavLink to="/compare">Compare</NavLink>
        <NavLink to="/launch">Launch</NavLink>
        <NavLink to="/evidence">Evidence</NavLink>
      </nav>
      <main>
        <Routes>
          <Route path="/" element={<Runs />} />
          <Route path="/runs/:id" element={<Replay />} />
          <Route path="/compare" element={<Compare />} />
          <Route path="/launch" element={<Launch />} />
          <Route path="/evidence" element={<Evidence />} />
          <Route path="*" element={<p className="note">No such page.</p>} />
        </Routes>
      </main>
    </BrowserRouter>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
