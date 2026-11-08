import "./styles/hero.css";
import { Hero } from "./components/Hero";
import { Nav } from "./components/Nav";

export function App() {
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <Nav onWatch={() => {}} />
      <main id="main">
        <Hero />
      </main>
    </>
  );
}
