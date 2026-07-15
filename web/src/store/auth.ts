import { create } from "zustand";

import { type AuthUser, login as apiLogin, setAuthToken } from "@/lib/api";

interface User {
  email: string;
  name: string;
  role: string;
}

interface AuthState {
  token: string | null;
  user: User | null;
  modalOpen: boolean;
  setModal: (v: boolean) => void;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  init: () => void;
}

const KEY = "sentigon_auth";

export const useAuth = create<AuthState>((set) => ({
  token: null,
  user: null,
  modalOpen: false,
  setModal: (modalOpen) => set({ modalOpen }),
  login: async (email, password) => {
    const r: AuthUser = await apiLogin(email, password);
    const user = { email: r.email, name: r.name, role: r.role };
    setAuthToken(r.access_token);
    try {
      localStorage.setItem(KEY, JSON.stringify({ token: r.access_token, user }));
    } catch {
      /* ignore storage errors */
    }
    set({ token: r.access_token, user, modalOpen: false });
  },
  logout: () => {
    setAuthToken(null);
    try {
      localStorage.removeItem(KEY);
    } catch {
      /* ignore */
    }
    set({ token: null, user: null });
  },
  init: () => {
    try {
      const raw = localStorage.getItem(KEY);
      if (raw) {
        const { token, user } = JSON.parse(raw);
        setAuthToken(token);
        set({ token, user });
      }
    } catch {
      /* ignore */
    }
  },
}));
