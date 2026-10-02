"use client";

import { apiUrl } from "@/app/lib/api";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

type ScenarioContextValue = {
  currentScenario: string;
  setCurrentScenario: (scenario: string) => void;
  scenarios: string[];
  addScenario: (name: string) => Promise<void>;
  deleteScenario: (name: string) => Promise<void>;
};

const ScenarioContext = createContext<ScenarioContextValue | null>(null);

export function ScenarioProvider({ children }: { children: ReactNode }) {
  const [currentScenario, setCurrentScenarioState] = useState("默认");
  const [scenarios, setScenarios] = useState<string[]>([]);

  const setCurrentScenario = useCallback((scenario: string) => {
    const normalized = scenario.trim() || "默认";
    setCurrentScenarioState(normalized);
    window.localStorage.setItem("musicer.current-scenario", normalized);
  }, []);

  useEffect(() => {
    let cancelled = false;
    void fetch(apiUrl("/api/scenarios"), { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error("scenario list unavailable");
        return (await response.json()) as { scenarios?: string[] };
      })
      .then((payload) => {
        if (cancelled || !Array.isArray(payload.scenarios)) return;
        const available = payload.scenarios.filter(
          (scenario): scenario is string => typeof scenario === "string" && Boolean(scenario)
        );
        setScenarios(available);
        const remembered = window.localStorage.getItem("musicer.current-scenario");
        setCurrentScenarioState(
          remembered && available.includes(remembered)
            ? remembered
            : available[0] || "默认"
        );
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const addScenario = useCallback(
    async (name: string) => {
      const normalized = name.trim();
      if (!normalized) return;
      try {
        const response = await fetch(apiUrl("/api/scenarios"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: normalized }),
        });
        if (!response.ok) return;
        const payload = (await response.json()) as { scenarios?: string[] };
        if (Array.isArray(payload.scenarios)) setScenarios(payload.scenarios);
        setCurrentScenario(normalized);
      } catch {
        // 场景服务不可用时保留现有状态。
      }
    },
    [setCurrentScenario]
  );

  const deleteScenario = useCallback(
    async (name: string) => {
      try {
        const response = await fetch(
          apiUrl(`/api/scenarios/${encodeURIComponent(name)}`),
          { method: "DELETE" }
        );
        if (!response.ok) return;
        const payload = (await response.json()) as { scenarios?: string[] };
        if (!Array.isArray(payload.scenarios)) return;
        setScenarios(payload.scenarios);
        if (currentScenario === name) {
          setCurrentScenario(payload.scenarios[0] || "默认");
        }
      } catch {
        // 删除失败时保留现有场景。
      }
    },
    [currentScenario, setCurrentScenario]
  );

  const value = useMemo(
    () => ({
      currentScenario,
      setCurrentScenario,
      scenarios,
      addScenario,
      deleteScenario,
    }),
    [addScenario, currentScenario, deleteScenario, scenarios, setCurrentScenario]
  );

  return <ScenarioContext.Provider value={value}>{children}</ScenarioContext.Provider>;
}

export function useScenario() {
  const value = useContext(ScenarioContext);
  if (!value) throw new Error("useScenario must be used within ScenarioProvider");
  return value;
}
