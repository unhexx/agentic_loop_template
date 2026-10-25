import { Agent, CursorAgentError, type SDKMessage } from "@cursor/sdk";

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value?.trim()) {
    console.error(`missing ${name}`);
    process.exit(1);
  }
  return value.trim();
}

function writeAssistant(event: SDKMessage): void {
  if (event.type !== "assistant") {
    return;
  }
  for (const block of event.message.content) {
    if (block.type === "text") {
      process.stdout.write(block.text);
    }
  }
}

async function main(): Promise<void> {
  const prompt = process.argv.slice(2).join(" ").trim();
  if (!prompt) {
    console.error('usage: npm run agent -- "<prompt>"');
    process.exit(1);
  }

  const apiKey = requireEnv("CURSOR_API_KEY");
  const modelId = process.env.CURSOR_MODEL?.trim() || "composer-2.5";
  const cwd = process.env.CURSOR_CWD?.trim() || process.cwd();

  try {
    await using agent = await Agent.create({
      apiKey,
      model: { id: modelId },
      local: { cwd, settingSources: [] },
    });

    const run = await agent.send(prompt);
    console.error(`agent=${agent.agentId} run=${run.id}`);

    for await (const event of run.stream()) {
      writeAssistant(event);
    }

    const result = await run.wait();
    process.stdout.write("\n");
    if (result.status === "error") {
      console.error(`run failed: ${result.id}`);
      process.exit(2);
    }
    if (result.status !== "finished") {
      console.error(`run ${result.status}: ${result.id}`);
      process.exit(2);
    }
  } catch (err) {
    if (err instanceof CursorAgentError) {
      console.error(
        `startup failed: ${err.message}, retryable=${err.isRetryable}`,
      );
      process.exit(1);
    }
    throw err;
  }
}

await main();
