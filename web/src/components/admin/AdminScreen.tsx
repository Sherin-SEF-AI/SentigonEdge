"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { core } from "@/lib/api";
import { pushSupported, subscribeToPush, testPush } from "@/lib/push";
import { useAuth } from "@/store/auth";

const ROLES = ["admin", "investigator", "operator", "viewer"];

function PushControls() {
  const [status, setStatus] = useState<{ ok: boolean; detail: string } | null>(null);
  const [busy, setBusy] = useState<"" | "sub" | "test">("");
  const supported = pushSupported();

  return (
    <div className="mb-4 border border-line bg-panel p-3">
      <div className="text-[12px] font-semibold text-fg">Operator Notifications</div>
      <div className="mt-0.5 text-[11px] text-fg-muted">
        Subscribe this browser to real-time push, then send a test to confirm delivery. P1 threats
        push automatically.
      </div>
      <div className="mt-3 flex items-center gap-2">
        <button
          disabled={!supported || busy !== ""}
          onClick={async () => {
            setBusy("sub");
            setStatus(await subscribeToPush());
            setBusy("");
          }}
          className="mono rounded-[3px] border border-line bg-raised px-3 py-1.5 text-[11px] uppercase text-cyan hover:bg-base disabled:opacity-40"
        >
          {busy === "sub" ? "enabling..." : "enable push"}
        </button>
        <button
          disabled={busy !== ""}
          onClick={async () => {
            setBusy("test");
            setStatus(await testPush());
            setBusy("");
          }}
          className="mono rounded-[3px] border border-line bg-raised px-3 py-1.5 text-[11px] uppercase text-fg-secondary hover:bg-base disabled:opacity-40"
        >
          {busy === "test" ? "sending..." : "send test push"}
        </button>
        {!supported && <span className="mono text-[10px] text-amber">not supported in this browser</span>}
      </div>
      {status && (
        <div className={`mono mt-2 text-[11px] ${status.ok ? "text-green" : "text-red"}`}>
          {status.ok ? "OK" : "FAILED"}: {status.detail}
        </div>
      )}
    </div>
  );
}

function CreateUser({ onDone }: { onDone: () => void }) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [pw, setPw] = useState("");
  const [role, setRole] = useState("operator");
  const [err, setErr] = useState("");
  const m = useMutation({
    mutationFn: () => core.createUser({ email, full_name: name, password: pw, role }),
    onSuccess: () => {
      setEmail("");
      setName("");
      setPw("");
      setErr("");
      onDone();
    },
    onError: (e: Error) => setErr(e.message === "409" ? "email exists" : "failed"),
  });
  return (
    <div className="mb-4 flex flex-wrap items-end gap-2 border border-line bg-panel p-3">
      <div>
        <label className="mb-1 block text-[10px] uppercase text-fg-muted">email</label>
        <input value={email} onChange={(e) => setEmail(e.target.value)}
          className="rounded-[3px] border border-line bg-base px-2 py-1 text-[12px] text-fg outline-none" />
      </div>
      <div>
        <label className="mb-1 block text-[10px] uppercase text-fg-muted">name</label>
        <input value={name} onChange={(e) => setName(e.target.value)}
          className="rounded-[3px] border border-line bg-base px-2 py-1 text-[12px] text-fg outline-none" />
      </div>
      <div>
        <label className="mb-1 block text-[10px] uppercase text-fg-muted">password</label>
        <input type="password" value={pw} onChange={(e) => setPw(e.target.value)}
          className="rounded-[3px] border border-line bg-base px-2 py-1 text-[12px] text-fg outline-none" />
      </div>
      <div>
        <label className="mb-1 block text-[10px] uppercase text-fg-muted">role</label>
        <select value={role} onChange={(e) => setRole(e.target.value)}
          className="rounded-[3px] border border-line bg-base px-2 py-1 text-[12px] text-fg outline-none">
          {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
      </div>
      <button disabled={m.isPending || !email || !pw}
        onClick={() => m.mutate()}
        className="mono rounded-[3px] bg-raised px-3 py-1.5 text-[11px] text-cyan hover:bg-base disabled:opacity-40">
        {m.isPending ? "creating..." : "create user"}
      </button>
      {err && <span className="text-[11px] text-red">{err}</span>}
    </div>
  );
}

export function AdminScreen() {
  const qc = useQueryClient();
  const user = useAuth((s) => s.user);
  const token = useAuth((s) => s.token);
  const setModal = useAuth((s) => s.setModal);

  const usersQ = useQuery({
    queryKey: ["admin-users"],
    queryFn: ({ signal }) => core.users(token!, signal),
    enabled: !!token && user?.role === "admin",
    retry: false,
  });
  const auditQ = useQuery({
    queryKey: ["admin-audit"],
    queryFn: ({ signal }) => core.audit(token!, 60, signal),
    enabled: !!token && user?.role === "admin",
    refetchInterval: 5000,
    retry: false,
  });
  const patch = useMutation({
    mutationFn: ({ id, body }: { id: string; body: { role?: string; is_active?: boolean } }) =>
      core.patchUser(id, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin-users"] }),
  });

  if (!user || user.role !== "admin") {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3">
        <div className="text-[13px] text-fg-secondary">Admin access required.</div>
        <button onClick={() => setModal(true)}
          className="mono rounded-[3px] border border-line bg-panel px-3 py-1.5 text-[12px] text-cyan hover:bg-raised">
          sign in as admin
        </button>
      </div>
    );
  }

  const users = usersQ.data ?? [];
  const audit = auditQ.data ?? [];

  return (
    <div className="flex h-full overflow-hidden">
      <div className="flex-1 overflow-auto p-4">
        <PushControls />
        <div className="mb-3 text-[13px] font-semibold text-fg">Users &amp; Roles</div>
        <CreateUser onDone={() => qc.invalidateQueries({ queryKey: ["admin-users"] })} />
        <table className="w-full">
          <thead>
            <tr className="border-b border-line text-left text-[10px] uppercase text-fg-muted">
              <th className="py-1 font-normal">email</th>
              <th className="py-1 font-normal">name</th>
              <th className="py-1 font-normal">role</th>
              <th className="py-1 font-normal">active</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} className="border-b border-line/50">
                <td className="py-1.5 text-[12px] text-fg">{u.email}</td>
                <td className="py-1.5 text-[12px] text-fg-secondary">{u.full_name}</td>
                <td className="py-1.5">
                  <select
                    value={u.role}
                    onChange={(e) => patch.mutate({ id: u.id, body: { role: e.target.value } })}
                    className="rounded-[3px] border border-line bg-base px-1.5 py-0.5 text-[11px] text-cyan outline-none"
                  >
                    {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                  </select>
                </td>
                <td className="py-1.5">
                  <button
                    onClick={() => patch.mutate({ id: u.id, body: { is_active: !u.is_active } })}
                    className={`mono rounded-[2px] px-1.5 py-0.5 text-[10px] uppercase ${
                      u.is_active ? "bg-green/15 text-green" : "bg-red/15 text-red"
                    }`}
                  >
                    {u.is_active ? "active" : "disabled"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="w-[380px] shrink-0 overflow-auto border-l border-line p-4">
        <div className="mb-2 text-[11px] uppercase tracking-[0.15em] text-fg-secondary">
          Audit log (live)
        </div>
        <div className="space-y-1">
          {audit.map((a) => (
            <div key={a.id} className="border-b border-line/40 pb-1 text-[11px]">
              <div className="flex justify-between">
                <span className="mono text-cyan">{a.action}</span>
                <span className="mono text-fg-muted">{a.ts?.slice(11, 19)}</span>
              </div>
              <div className="text-fg-muted">
                {a.resource_type}
                {a.details ? ` · ${JSON.stringify(a.details).slice(0, 60)}` : ""}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
