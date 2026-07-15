"use client";

import { Bell, LogOut, User } from "lucide-react";
import { useState } from "react";

import { subscribeToPush } from "@/lib/push";
import { useAuth } from "@/store/auth";

function PushBell() {
  const [state, setState] = useState<"idle" | "on" | "busy">("idle");
  return (
    <button
      title={state === "on" ? "push notifications enabled" : "enable push notifications"}
      disabled={state === "busy"}
      onClick={async () => {
        setState("busy");
        const r = await subscribeToPush();
        setState(r.ok ? "on" : "idle");
      }}
      className={`px-1.5 ${state === "on" ? "text-cyan" : "text-fg-muted hover:text-fg"}`}
    >
      <Bell size={12} />
    </button>
  );
}

function LoginModal() {
  const open = useAuth((s) => s.modalOpen);
  const setModal = useAuth((s) => s.setModal);
  const login = useAuth((s) => s.login);
  const [email, setEmail] = useState("admin@sentigon.local");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[120] flex items-start justify-center bg-black/60 pt-[16vh]"
      onMouseDown={() => setModal(false)}
    >
      <div
        className="w-[360px] border border-line bg-raised p-4 shadow-2xl"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center gap-2 text-[13px] font-semibold text-fg">
          <span className="inline-block h-2.5 w-2.5 rounded-[2px] bg-cyan" />
          Sign in to Sentigon
        </div>
        <label className="mb-1 block text-[10px] uppercase tracking-wide text-fg-muted">email</label>
        <input
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="mb-2 w-full rounded-[3px] border border-line bg-base px-2 py-1.5 text-[13px] text-fg outline-none"
        />
        <label className="mb-1 block text-[10px] uppercase tracking-wide text-fg-muted">password</label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          className="mb-3 w-full rounded-[3px] border border-line bg-base px-2 py-1.5 text-[13px] text-fg outline-none"
        />
        {err && <div className="mb-2 text-[12px] text-red">{err}</div>}
        <button
          disabled={busy}
          onClick={submit}
          className="mono w-full rounded-[3px] bg-panel py-1.5 text-[12px] text-cyan hover:bg-base disabled:opacity-40"
        >
          {busy ? "signing in..." : "sign in"}
        </button>
      </div>
    </div>
  );

  async function submit() {
    setErr("");
    setBusy(true);
    try {
      await login(email, password);
    } catch {
      setErr("invalid credentials");
    } finally {
      setBusy(false);
    }
  }
}

export function AuthBar() {
  const user = useAuth((s) => s.user);
  const setModal = useAuth((s) => s.setModal);
  const logout = useAuth((s) => s.logout);
  return (
    <>
      <div className="ml-auto flex items-center pr-1">
        <PushBell />
        {user ? (
          <div className="flex items-center gap-2 px-2 text-[11px]">
            <User size={12} className="text-fg-muted" />
            <span className="text-fg-secondary">{user.email}</span>
            <span className="mono rounded-[2px] bg-raised px-1.5 py-0.5 text-[10px] uppercase text-cyan">
              {user.role}
            </span>
            <button onClick={logout} title="sign out" className="text-fg-muted hover:text-fg">
              <LogOut size={12} />
            </button>
          </div>
        ) : (
          <button
            onClick={() => setModal(true)}
            className="mono px-2 text-[11px] text-fg-muted hover:text-cyan"
          >
            sign in
          </button>
        )}
      </div>
      <LoginModal />
    </>
  );
}
