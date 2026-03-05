"use client";
/**
 * AuthContext — stores JWT token in localStorage, provides login/logout.
 */
import React, { createContext, useContext, useEffect, useState } from "react";
import { login as apiLogin, getMe } from "@/lib/api";

interface AuthContextValue {
  token: string | null;
  role: string | null;
  email: string | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [role, setRole]   = useState<string | null>(null);
  const [email, setEmail] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const stored = localStorage.getItem("admin_token");
    if (stored) {
      setToken(stored);
      getMe()
        .then((me) => { setEmail(me.email); setRole(me.role); })
        .catch(() => { localStorage.removeItem("admin_token"); setToken(null); })
        .finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
  }, []);

  const login = async (emailInput: string, password: string) => {
    const res = await apiLogin(emailInput, password);
    localStorage.setItem("admin_token", res.access_token);
    setToken(res.access_token);
    setRole(res.role);
    setEmail(emailInput);
  };

  const logout = () => {
    localStorage.removeItem("admin_token");
    setToken(null);
    setRole(null);
    setEmail(null);
    window.location.href = "/login";
  };

  return (
    <AuthContext.Provider value={{ token, role, email, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
