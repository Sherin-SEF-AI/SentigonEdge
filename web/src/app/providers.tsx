"use client";

import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

import { useAuth } from "@/store/auth";

// getJSON/postJSON throw `Error("<status> <statusText>")`, so a 401 surfaces as a
// message starting with "401". Reads are authenticated now: on any 401 clear the
// stale/absent token and open the login modal so the operator is prompted, instead
// of the console silently failing every poll.
function isUnauthorized(error: unknown): boolean {
  return error instanceof Error && error.message.startsWith("401");
}

function handleAuthError(error: unknown) {
  if (isUnauthorized(error)) {
    const auth = useAuth.getState();
    auth.logout();
    auth.setModal(true);
  }
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        queryCache: new QueryCache({ onError: handleAuthError }),
        mutationCache: new MutationCache({ onError: handleAuthError }),
        defaultOptions: {
          queries: {
            // don't retry a 401 (it will just 401 again) — surface it immediately
            retry: (failureCount, error) => !isUnauthorized(error) && failureCount < 1,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
