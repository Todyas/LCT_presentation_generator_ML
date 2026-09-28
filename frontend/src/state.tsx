import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import type { TemplateDna } from "./normalize";
import type { PurposeId } from "./ui";

export type Session = {
  jobId: string;
  templateName: string;
  fileSize: string;
  brief: string;
  purpose: PurposeId;
  slideCount: number;
  dna: TemplateDna | null;
};

const STORAGE_KEY = "shmyaks-session";

function readSession(): Session | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

type SessionContextValue = {
  session: Session | null;
  setSession: (session: Session) => void;
};

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSessionState] = useState<Session | null>(readSession);

  const value = useMemo<SessionContextValue>(
    () => ({
      session,
      setSession: (next) => {
        setSessionState(next);
        sessionStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      },
    }),
    [session],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession() {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession must be used within SessionProvider");
  return value;
}
