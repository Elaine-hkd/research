import type { Context } from '@deepseek-ai/cordis'
import { defineTool } from '@deepseek-ai/dsh-tools'

export const name = 'registry-tools'
export const inject = ['tools']

export function apply(ctx: Context) {
    const base = process.env.SKILL_REGISTRY_URL
    if (!base) throw new Error('Missing SKILL_REGISTRY_URL')

    let raw = process.env.EXPOSED_SKILLS
    if (raw === undefined) {
        raw = '[]'
    }

    const exposedSkills: unknown = JSON.parse(raw)
    if (
        !Array.isArray(exposedSkills) ||
        !exposedSkills.every(
            (name) => typeof name === 'string' && name.trim().length > 0,
        )
    ) {
        throw new Error('EXPOSED_SKILLS must be a JSON array of skill names')
    }

    async function request(
        endpoint: string,
        params: Record<string, string>,
        signal: AbortSignal,
    ): Promise<string> {
        const url = new URL(endpoint, base)
        url.search = new URLSearchParams(params).toString()

        const response = await fetch(url, {
            signal: AbortSignal.any([
                signal,
                AbortSignal.timeout(30_000),
            ]),
        })

        if (!response.ok) {
            throw new Error(`Registry HTTP ${response.status}`)
        }

        // 将注册中心返回的 JSON 作为文本交给模型。
        return JSON.stringify(await response.json())
    }

    ctx.tools.register(defineTool({
        name: 'search_skills',
        description:
            'Search the skill registry and return skill summaries without loading ' +
            'full instructions. Use load_skill to load a selected skill.',
        parameters: {
            query: {
                type: 'string',
                description: 'Search query; defaults to an empty string',
            },
            exposed_skills: {
                type: 'array',
                items: { type: 'string' },
                description: 'Restrict results to these skill names; omit for no caller filter',
            },
        },
        output: {
            schema: { type: 'string' },
            render: (_args, value) => [{ type: 'text', text: value }],
        },
        async execute(args, exec) {
            const url = new URL('/search_skills', base)
            url.searchParams.set('query', args.query ?? '')
            url.searchParams.set(
                'exposed_skills',
                JSON.stringify(exposedSkills),
            )

            const response = await fetch(url, {
                method: 'GET',
                signal: AbortSignal.any([
                    exec.signal,
                    AbortSignal.timeout(30_000),
                ]),
            })

            const result = await response.text()

            if (!response.ok) {
                throw new Error(`search_skills HTTP ${response.status}: ${result}`)
            }

            return result
        },
    }))

    ctx.tools.register(defineTool({
        name: 'load_skill',
        description: 'Load a skill’s instructions and resource list by name.',
        parameters: {
            name: {
                type: 'string',
                required: true,
                description: 'Skill name from the available skills catalog',
            },
        },
        output: {
            schema: { type: 'string' },
            render: (_args, value) => [{ type: 'text', text: value }],
        },
        async execute(args, exec) {
            return request(
                '/load_skill',
                { skill_name: args.name },
                exec.signal,
            )
        },
    }))

    ctx.tools.register(defineTool({
        name: 'load_resource',
        description: 'Read a resource belonging to a skill.',
        parameters: {
            name: { type: 'string', required: true },
            relative_path: { type: 'string', required: true },
        },
        output: {
            schema: { type: 'string' },
            render: (_args, value) => [{ type: 'text', text: value }],
        },
        async execute(args, exec) {
            return request(
                '/load_resource',
                {
                    skill_name: args.name,
                    relative_path: args.relative_path,
                },
                exec.signal,
            )
        },
    }))

    ctx.tools.register(defineTool({
        name: 'execute_resource',
        description:
            'Execute a script or executable resource belonging to a skill. ' +
            'Pass command-line arguments as separate array elements. ' +
            'Execution takes place in the environment managed by the registry server.',
        parameters: {
            skill_name: {
                type: 'string',
                required: true,
                description: 'Name of the skill owning the resource',
            },
            relative_path: {
                type: 'string',
                required: true,
                description: 'Resource path relative to the skill root',
            },
            arguments: {
                type: 'array',
                items: { type: 'string' },
                description: 'Command-line arguments; defaults to []',
            },
            timeout: {
                type: 'number',
                description: 'Execution timeout in seconds; omit for server default',
            },
        },
        output: {
            schema: { type: 'string' },
            render: (_args, value) => [{ type: 'text', text: value }],
        },
        async execute(args, exec) {
            if (!args.skill_name.trim() || !args.relative_path.trim()) {
                throw new Error('skill_name and relative_path must not be empty')
            }

            if (
                args.timeout !== undefined &&
                (!Number.isFinite(args.timeout) || args.timeout <= 0)
            ) {
                throw new Error('timeout must be a positive finite number')
            }

            const body = {
                skill_name: args.skill_name,
                relative_path: args.relative_path,
                arguments: args.arguments ?? [],
                ...(args.timeout !== undefined
                    ? { timeout: args.timeout }
                    : {}),
            }

            // 不使用前面读取接口的 30 秒超时，避免截断长时间执行。
            // 此处不自动重试，以免重复执行资源。
            const response = await fetch(new URL('/execute_resource', base), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
                signal: exec.signal,
            })

            const result = await response.text()

            if (!response.ok) {
                throw new Error(
                    `execute_resource HTTP ${response.status}: ${result}`,
                )
            }

            return result
        },
    }))
}