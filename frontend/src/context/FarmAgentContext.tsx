import { createContext, useContext, useState, type ReactNode } from 'react';
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

export function useFarmAgentContext() {
  const ctx = useContext(FarmAgentContext);
  if (!ctx) {
    throw new Error('useFarmAgentContext must be used inside <FarmAgentProvider>');
  }
  return ctx;
}
