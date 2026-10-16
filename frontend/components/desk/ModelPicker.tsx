"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type Model = { slug: string; display_name: string };
type Status = {
  provider: string; model: string; state: string; chatgpt_state?: string; message: string;
  models: Model[]; openrouter_models?: Model[]; openrouter_available?: boolean;
};

const LABELS: Record<string, string> = { chatgpt_plan: "ChatGPT", openrouter: "OpenRouter", openai: "OpenAI API" };

/** Chooses the provider and model for the next AI request. The backend holds every key and token; a refused choice leaves the current one in place. */
export default function ModelPicker({ running }: { running: boolean }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const catalogAsked = useRef(false);

  const call = useCallback(async (path: string, body?: object) => {
    const response = await fetch(`/api/ai/${path}`, body === undefined ? { cache: "no-store" } : {
      method: "POST", headers: { "Content-Type": "application/json", "X-TFS-Local": "1" }, body: JSON.stringify(body),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "The AI connection is unavailable. Try again.");
    setStatus(result as Status);
    return result as Status;
  }, []);

  const load = useCallback(async () => {
    try {
      const current = await call("connection");
      // A saved ChatGPT sign-in lists its models only after one catalog read.
      if ((current.chatgpt_state ?? current.state) === "connected" && !current.models.length && !catalogAsked.current) {
        catalogAsked.current = true;
        await call("refresh", {}).catch(() => undefined);
      }
    } catch { setError("AI connection status is unavailable. Check the backend and retry."); }
  }, [call]);

  useEffect(() => {
    void load();
    const refresh = () => void load();
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
  }, [load]);

  async function choose(value: string) {
    const [provider, ...rest] = value.split("|");
    setBusy(true); setError("");
    try { await call("provider", { provider, model: rest.join("|") }); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "That model could not be selected. Try again."); }
    finally { setBusy(false); }
  }

  if (!status) return null;
  const chatgptReady = (status.chatgpt_state ?? status.state) === "connected" && status.models.length > 0;
  const current = `${status.provider}|${status.model}`;
  const listed = [...(status.openrouter_models ?? []).map(m => `openrouter|${m.slug}`), ...status.models.map(m => `chatgpt_plan|${m.slug}`)];
  const ready = status.state === "connected";
  return <span className="model-pick">
    <span className="model-row"><span className="live" data-state={ready ? "connected" : "failed"} aria-hidden="true" />
    <label htmlFor="ai-model" className="sr-only">AI model</label>
    <select id="ai-model" className="in pill-select" value={current} disabled={running || busy} title={`${LABELS[status.provider] ?? status.provider} · ${status.model || "no model"}`}
      aria-describedby={error || !ready ? "ai-model-note" : undefined} onFocus={() => void load()} onChange={event => void choose(event.target.value)}>
      {!listed.includes(current) && <option value={current}>{LABELS[status.provider] ?? status.provider} · {status.model || "Choose a model"}</option>}
      <optgroup label={status.openrouter_available ? "OpenRouter" : "OpenRouter (add OPENROUTER_API_KEY to backend/.env)"}>
        {(status.openrouter_models ?? []).map(m => <option key={m.slug} value={`openrouter|${m.slug}`} disabled={!status.openrouter_available}>{m.display_name}</option>)}
      </optgroup>
      <optgroup label="ChatGPT">
        {chatgptReady ? status.models.map(m => <option key={m.slug} value={`chatgpt_plan|${m.slug}`}>{m.display_name}</option>)
          : <option value="chatgpt_plan|" disabled>Sign in under AI connection</option>}
      </optgroup>
    </select></span>
    {(error || !ready) && <span id="ai-model-note" role={error ? "alert" : "status"} className="cap amber model-note">{error || "AI is not connected. Open AI connection to fix it."}</span>}
  </span>;
}
