import { useRef } from "react";
import "./styles/hero.css";
import "./styles/pipeline.css";
import "./styles/sections.css";
import { Hero } from "./components/Hero";
import { Nav } from "./components/Nav";
import { Pipeline, type PipelineHandle } from "./components/Pipeline";
import { Aws, Closing, Footer, Why } from "./components/Sections";
import { Swarms } from "./components/Swarms";

export function App() {
  const pipeline = useRef<PipelineHandle>(null);
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <Nav onWatch={() => pipeline.current?.restart()} />
      <main id="main">
        <Hero />
        <Pipeline ref={pipeline} />
        <Why />
        <Swarms />
        <Aws />
        <Closing />
      </main>
      <Footer />
    </>
  );
}
