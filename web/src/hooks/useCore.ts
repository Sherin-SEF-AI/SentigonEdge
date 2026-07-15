"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { core } from "@/lib/api";
import { useAuth } from "@/store/auth";

export function useIncidents(status?: string) {
  return useQuery({
    queryKey: ["incidents", status ?? "all"],
    queryFn: ({ signal }) => core.incidents(status, signal),
    refetchInterval: 3000,
  });
}

export function useIncident(id: string | null) {
  return useQuery({
    queryKey: ["incident", id],
    queryFn: ({ signal }) => core.incident(id as string, signal),
    enabled: !!id,
    refetchInterval: 4000,
  });
}

export function useCoreSummary() {
  return useQuery({
    queryKey: ["core-summary"],
    queryFn: ({ signal }) => core.summary(signal),
    refetchInterval: 3000,
  });
}

export function useSignatures() {
  return useQuery({
    queryKey: ["signatures"],
    queryFn: ({ signal }) => core.signatures(signal),
    refetchInterval: 15000,
  });
}

export function useIncidentAction() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, action }: { id: string; action: string }) => core.action(id, action),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["incidents"] });
      qc.invalidateQueries({ queryKey: ["incident"] });
      qc.invalidateQueries({ queryKey: ["core-summary"] });
    },
    onError: (e: Error) => {
      if (e.message.startsWith("401")) useAuth.getState().setModal(true);
    },
  });
}
