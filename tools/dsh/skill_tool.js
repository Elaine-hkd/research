import { defineTool } from "@deepseek-ai/dsh-tools";
const name = "registry-tools";
const inject = ["tools"];
function apply(ctx) {
  const base = process.env.SKILL_REGISTRY_URL;
  if (!base) throw new Error("Missing SKILL_REGISTRY_URL");
  let raw = process.env.EXPOSED_SKILLS;
  if (raw === void 0) {
    raw = "[]";
  }
  const exposedSkills = JSON.parse(raw);
  if (!Array.isArray(exposedSkills) || !exposedSkills.every(
    (name2) => typeof name2 === "string" && name2.trim().length > 0
  )) {
    throw new Error("EXPOSED_SKILLS must be a JSON array of skill names");
  }
  async function request(endpoint, params, signal) {
    const url = new URL(endpoint, base);
    url.search = new URLSearchParams(params).toString();
    const response = await fetch(url, {
      signal: AbortSignal.any([
        signal,
        AbortSignal.timeout(3e4)
      ])
    });
    if (!response.ok) {
      throw new Error(`Registry HTTP ${response.status}`);
    }
    return JSON.stringify(await response.json());
  }
  ctx.tools.register(defineTool({
    name: "search_skills",
    description: "Search the skill registry and return skill summaries without loading full instructions. Use load_skill to load a selected skill.",
    parameters: {
      query: {
        type: "string",
        description: "Search query; defaults to an empty string"
      },
      exposed_skills: {
        type: "array",
        items: { type: "string" },
        description: "Restrict results to these skill names; omit for no caller filter"
      }
    },
    output: {
      schema: { type: "string" },
      render: (_args, value) => [{ type: "text", text: value }]
    },
    async execute(args, exec) {
      const url = new URL("/search_skills", base);
      url.searchParams.set("query", args.query ?? "");
      url.searchParams.set(
        "exposed_skills",
        JSON.stringify(exposedSkills)
      );
      const response = await fetch(url, {
        method: "GET",
        signal: AbortSignal.any([
          exec.signal,
          AbortSignal.timeout(3e4)
        ])
      });
      const result = await response.text();
      if (!response.ok) {
        throw new Error(`search_skills HTTP ${response.status}: ${result}`);
      }
      return result;
    }
  }));
  ctx.tools.register(defineTool({
    name: "load_skill",
    description: "Load a skill\u2019s instructions and resource list by name.",
    parameters: {
      name: {
        type: "string",
        required: true,
        description: "Skill name from the available skills catalog"
      }
    },
    output: {
      schema: { type: "string" },
      render: (_args, value) => [{ type: "text", text: value }]
    },
    async execute(args, exec) {
      return request(
        "/load_skill",
        { skill_name: args.name },
        exec.signal
      );
    }
  }));
  ctx.tools.register(defineTool({
    name: "load_resource",
    description: "Read a resource belonging to a skill.",
    parameters: {
      name: { type: "string", required: true },
      relative_path: { type: "string", required: true }
    },
    output: {
      schema: { type: "string" },
      render: (_args, value) => [{ type: "text", text: value }]
    },
    async execute(args, exec) {
      return request(
        "/load_resource",
        {
          skill_name: args.name,
          relative_path: args.relative_path
        },
        exec.signal
      );
    }
  }));
  ctx.tools.register(defineTool({
    name: "execute_resource",
    description: "Execute a script or executable resource belonging to a skill. Pass command-line arguments as separate array elements. Execution takes place in the environment managed by the registry server.",
    parameters: {
      skill_name: {
        type: "string",
        required: true,
        description: "Name of the skill owning the resource"
      },
      relative_path: {
        type: "string",
        required: true,
        description: "Resource path relative to the skill root"
      },
      arguments: {
        type: "array",
        items: { type: "string" },
        description: "Command-line arguments; defaults to []"
      },
      timeout: {
        type: "number",
        description: "Execution timeout in seconds; omit for server default"
      }
    },
    output: {
      schema: { type: "string" },
      render: (_args, value) => [{ type: "text", text: value }]
    },
    async execute(args, exec) {
      if (!args.skill_name.trim() || !args.relative_path.trim()) {
        throw new Error("skill_name and relative_path must not be empty");
      }
      if (args.timeout !== void 0 && (!Number.isFinite(args.timeout) || args.timeout <= 0)) {
        throw new Error("timeout must be a positive finite number");
      }
      const body = {
        skill_name: args.skill_name,
        relative_path: args.relative_path,
        arguments: args.arguments ?? [],
        ...args.timeout !== void 0 ? { timeout: args.timeout } : {}
      };
      const response = await fetch(new URL("/execute_resource", base), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: exec.signal
      });
      const result = await response.text();
      if (!response.ok) {
        throw new Error(
          `execute_resource HTTP ${response.status}: ${result}`
        );
      }
      return result;
    }
  }));
}
export {
  apply,
  inject,
  name
};
