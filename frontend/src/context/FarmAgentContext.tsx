import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { useFarmAgent } from '@/hooks/useFarmAgent';

// Why a context: the dashboard "에이전트에게 맡기기" buttons need to push
// prompts into the same conversation that's open in the side rail. Without
// a context provider, each useFarmAgent() call would create an isolated
// state slice and the dashboard's send() would land in a different chat
// than the user sees on the right.
//
// Also stores agentOpen so AppLayout, MobileNav, and any deep page can
// open the drawer without prop-drilling onOpenAgent everywhere.

type FarmAgentValue = ReturnType<typeof useFarmAgent> & {
  agentOpen: boolean;
  openAgent: () => void;
  closeAgent: () => void;
  sendAndOpen: (prompt: string) => Promise<void>;
};

const FarmAgentContext = createContext<FarmAgentValue | null>(null);

export function FarmAgentProvider({ children }: { children: ReactNode }) {
  const agent = useFarmAgent();
  const [agentOpen, setAgentOpen] = useState(false);

  // Single-fire briefing auto-load. Both DashboardPage and FarmAgentConsole used
  // to call fetchBriefing(false) in their own useEffects, racing two requests
  // and triggering two setBriefing → two re-renders (the "stutter"). Hoisting
  // the auto-fetch here guarantees one call per provider mount; pages now read
  // briefing from context without triggering their own fetch.
  //
  // The guard ref is set BEFORE the await and reset in the effect cleanup so
  // React 18 StrictMode (dev) — which mounts → unmounts → re-mounts effects —
  // still triggers exactly one fetch on the second mount. Without the cleanup
  // reset, the first mount would set the ref to true, the cleanup would fire,
  // and the second mount would skip the fetch entirely.
  const fetchedOnceRef = useRef(false);
  useEffect(() => {
    if (fetchedOnceRef.current) return;
    fetchedOnceRef.current = true;
    void agent.fetchBriefing(false);
    return () => {
      fetchedOnceRef.current = false;
    };
  }, [agent.fetchBriefing]);

  const value: FarmAgentValue = {
    ...agent,
    agentOpen,
    openAgent: () => setAgentOpen(true),
    closeAgent: () => setAgentOpen(false),
    sendAndOpen: async (prompt: string) => {
      setAgentOpen(true);
      await agent.send(prompt);
    },
  };

  return <FarmAgentContext.Provider value={value}>{children}</FarmAgentContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useFarmAgentContext() {
  const ctx = useContext(FarmAgentContext);
  if (!ctx) {
    throw new Error('useFarmAgentContext must be used inside <FarmAgentProvider>');
  }
  return ctx;
}
