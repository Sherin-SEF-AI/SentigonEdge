"use client";

import { useQuery } from "@tanstack/react-query";

import { ingest } from "@/lib/api";

export function useStreams() {
  return useQuery({
    queryKey: ["streams"],
    queryFn: ({ signal }) => ingest.streams(signal),
    refetchInterval: 2000,
    staleTime: 1000,
  });
}

export function useSummary() {
  return useQuery({
    queryKey: ["ingest-summary"],
    queryFn: ({ signal }) => ingest.summary(signal),
    refetchInterval: 2000,
  });
}
