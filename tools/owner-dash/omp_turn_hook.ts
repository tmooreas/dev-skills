// omp hook: `owner-dash turn` at the start of every main-session prompt, and every minute while the session is open.
// Its output (a service down, an approval not acted on) goes to the orchestrator; silence means all is well.
// Installed by `owner-dash install`, which writes a one-line re-export of this file into ~/.omp/agent/hooks/pre/.
import { execFile } from "node:child_process";
import { fileURLToPath } from "node:url";

const BIN = fileURLToPath(new URL("../../bin/owner-dash", import.meta.url));

// The slice of omp's extension API this hook uses; kept local so the file needs no omp package to resolve.
interface Ctx {
	cwd: string;
	agent?: { kind: "main" | "sub" };
	setInterval(fn: () => Promise<void>, ms: number): unknown;
}
interface Message {
	customType: string;
	content: string;
	display: boolean;
}
interface Api {
	on(event: "session_start", handler: (event: unknown, ctx: Ctx) => Promise<void>): void;
	on(
		event: "before_agent_start",
		handler: (event: { reason?: string }, ctx: Ctx) => Promise<{ message: Message } | undefined>,
	): void;
	sendMessage(message: Message, options: { deliverAs: "aside"; triggerTurn: boolean }): void;
}

function check(cwd: string): Promise<string> {
	const { promise, resolve } = Promise.withResolvers<string>();
	execFile(BIN, ["turn"], { cwd, timeout: 120_000 }, (err, stdout) =>
		resolve(String(stdout).trim() || (err ? `owner-dash turn failed: ${err.message}` : "")),
	);
	return promise;
}

export default function (pi: Api): void {
	let last = "";
	pi.on("session_start", async (_event, ctx) => {
		if (ctx.agent?.kind !== "main") return;
		ctx.setInterval(async () => {
			const out = await check(ctx.cwd);
			if (out && out !== last) {
				pi.sendMessage({ customType: "owner-dash", content: out, display: true }, { deliverAs: "aside", triggerTurn: true });
			}
			last = out;
		}, 60_000);
	});
	pi.on("before_agent_start", async (event, ctx) => {
		if (ctx.agent?.kind !== "main" || event.reason === "revive") return undefined;
		last = await check(ctx.cwd);
		return last ? { message: { customType: "owner-dash", content: last, display: true } } : undefined;
	});
}
