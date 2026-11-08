import { createContext } from "react";
import type { PulseBus } from "../model/activity";

/**
 * Where squares hear that their agent made a call. The console's data
 * provider supplies the bus; without one (a test, a story) squares stay
 * still.
 */
export const PulseContext = createContext<PulseBus | null>(null);
