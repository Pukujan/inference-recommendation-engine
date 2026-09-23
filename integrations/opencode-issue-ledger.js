/*
 * OpenCode V1 project plugin template for the local Operational Issue Ledger.
 *
 * Install by copying this file into .opencode/plugins/ (or the global plugin
 * directory) and set:
 *   IRE_ISSUE_LEDGER_DB
 *   IRE_ISSUE_LEDGER_AGENT_SCRIPT
 *   IRE_ISSUE_LEDGER_PROVIDER / IRE_ISSUE_LEDGER_ROUTE (optional)
 *
 * The plugin reports session.error only. It intentionally ignores prompts,
 * message bodies, tool arguments, and response content. It is a report-only
 * adapter; the Python reducer still decides evidence level and authority.
 */

function text(value, fallback) {
  return typeof value === "string" && value.trim() ? value.trim() : fallback
}

async function reportFailure(event) {
  const db = process.env.IRE_ISSUE_LEDGER_DB || ".ire/issue-ledger/ledger.sqlite3"
  const agentScript = process.env.IRE_ISSUE_LEDGER_AGENT_SCRIPT
  if (!db || !agentScript || event?.type !== "session.error") return

  const sessionId = text(event.properties?.sessionID ?? event.sessionID, "unknown-session")
  const errorCode = text(event.properties?.error?.name ?? event.properties?.error?.code, "session.error")
  const python = process.env.IRE_ISSUE_LEDGER_PYTHON || "python"
  const args = [
    agentScript,
    "--db", db,
    "report",
    "--actor-id", `agent:opencode:${sessionId}`,
    "--actor-kind", "agent",
    "--harness", "opencode",
    "--provider", text(process.env.IRE_ISSUE_LEDGER_PROVIDER, "unknown-provider"),
    "--route", text(process.env.IRE_ISSUE_LEDGER_ROUTE, "opencode"),
    "--operation", "agent-session",
    "--workload-class", text(process.env.IRE_ISSUE_LEDGER_WORKLOAD_CLASS, "long_horizon"),
    "--stream-mode", text(process.env.IRE_ISSUE_LEDGER_STREAM_MODE, "unknown"),
    "--execution-id", `opencode-session:${sessionId}`,
    "--summary", "OpenCode reported a session error",
    "--classification", "harness",
    "--outcome", "failure",
    "--failure-phase", "request",
    "--error-code", errorCode,
  ]
  const child = Bun.spawn([python, ...args], { stdout: "ignore", stderr: "ignore" })
  await child.exited
}

export const InferenceRecommendationEngineIssueLedger = async () => ({
  event: async ({ event }) => {
    try {
      await reportFailure(event)
    } catch {
      // Reporting must not alter OpenCode's provider/session behavior.
    }
  },
})
