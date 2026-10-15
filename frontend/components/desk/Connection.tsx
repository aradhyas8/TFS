"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/arc/button/button";
import styles from "./Connection.module.css";

type ConnectionState = "disconnected" | "connecting" | "connected" | "expired" | "unavailable" | "failed";
type ConnectionStatus = {
  /** The provider that answers AI questions; chatgpt_state drives the ChatGPT sign-in controls. */
  state: ConnectionState; chatgpt_state?: ConnectionState;
  provider: string; model: string; account: string | null; message: string;
  models: { slug: string; display_name: string }[];
  api_key_available: boolean; api_provider: string;
};

const PROVIDERS: Record<string, string> = { chatgpt_plan: "ChatGPT plan", openrouter: "OpenRouter", openai: "OpenAI API" };

export default function Connection() {
  const [status, setStatus] = useState<ConnectionStatus | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [model, setModel] = useState("");
  const details = useRef<HTMLDetailsElement>(null);
  const request = useCallback(async (path = "connection", body?: object) => {
    const response = await fetch(`/api/ai/${path}`, path === "connection" ? { cache: "no-store" } : {
      method: "POST", headers: { "Content-Type": "application/json", "X-TFS-Local": "1" }, body: JSON.stringify(body ?? {}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Connection unavailable. Try again.");
    setStatus(result);
    return result as ConnectionStatus;
  }, []);
  useEffect(() => {
    let disposed = false;
    const poll = async () => {
      try { if (!disposed) await request(); } catch { if (!disposed) setError("Connection status unavailable. Check the backend and retry."); }
    };
    void poll();
    const timer = setInterval(() => void poll(), 3000);
    return () => { disposed = true; clearInterval(timer); };
  }, [request]);
  async function act(path: string, body?: object) {
    setBusy(true); setError("");
    if (details.current) details.current.open = true;
    try { await request(path, body); if (path === "logout") setModel(""); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Connection failed. Try again."); }
    finally { setBusy(false); }
  }
  const chatgpt = status?.chatgpt_state ?? status?.state;
  const connecting = chatgpt === "connecting";
  const connected = chatgpt === "connected";
  const label = status?.state ?? "loading";
  return <section className={styles.connection} aria-label="AI connection">
    <div className={styles.bar}>
      <span className={styles.caption}>AI · {PROVIDERS[status?.provider ?? ""] ?? "API-key provider"}</span>
      {status?.state !== "connected" && !connecting && chatgpt !== "unavailable" && <Button size="sm" loading={busy} onClick={() => void act("login")}>Continue with ChatGPT</Button>}
      <details ref={details} className={styles.details}>
        <summary>AI connection · <span data-state={label}>{label}</span></summary>
        <div className={styles.card}>
          <h2>Your AI connection</h2>
          <p>Ask questions using your ChatGPT plan or a backend API key. Your local portfolio is always available.</p>
          <p role="status" data-state={label}>{status?.message ?? "Checking the connection…"}</p>
          {status?.account && <p className={styles.account}>{status.account}</p>}
          {status?.model && <p>Selected model: <strong>{status.model}</strong></p>}
          {error && <p role="alert" className={styles.error}>{error}</p>}
          {connecting ? <Button variant="secondary" size="sm" loading={busy} onClick={() => void act("login/cancel")}>Cancel sign-in</Button>
            : <div className={styles.actions}>
              {chatgpt !== "unavailable" && <Button size="sm" loading={busy} onClick={() => void act("login")}>{connected || chatgpt === "expired" ? "Reconnect ChatGPT" : "Continue with ChatGPT"}</Button>}
              <Button variant="secondary" size="sm" disabled={busy} onClick={() => void act("refresh")}>Refresh connection</Button>
              {status?.account && <Button variant="ghost" size="sm" disabled={busy} onClick={() => void act("logout")}>Sign out of ChatGPT</Button>}
            </div>}
          {!!status?.models.length && <form className={styles.models} onSubmit={event => { event.preventDefault(); void act("provider", { provider: "chatgpt_plan", model: model || status.model }); }}>
            <label htmlFor="chatgpt-model">ChatGPT model</label>
            <select id="chatgpt-model" value={model || status.model} onChange={event => setModel(event.target.value)} disabled={busy || connecting}>
              <option value="">Choose a supported model</option>
              {status.models.map(item => <option key={item.slug} value={item.slug}>{item.display_name}</option>)}
            </select>
            <Button size="sm" variant="secondary" disabled={busy || connecting || !(model || status.model)} type="submit">Use ChatGPT plan</Button>
          </form>}
          <div className={styles.api}>
            <strong>API-key provider</strong>
            <p>{status?.api_key_available ? "Use the provider configured on this backend." : "Follow API-key setup in README.md, then restart TFS."}</p>
            {status?.api_key_available && <Button variant="secondary" size="sm" disabled={busy || connecting} onClick={() => void act("provider", { provider: status.api_provider })}>Use API-key provider</Button>}
          </div>
          <p className={styles.caption}>Supported ChatGPT-plan inference through TFS. Connection changes apply to future AI work after current work finishes.</p>
        </div>
      </details>
    </div>
    {error && !details.current?.open && <p role="alert" className={styles.error}>{error}</p>}
  </section>;
}
