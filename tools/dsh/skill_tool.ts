import type { Context } from '@deepseek-ai/cordis'
import { defineTool } from '@deepseek-ai/dsh-tools'

export const name = 'registry-tools'
export const inject = ['tools']

export function apply(ctx: Context) {
    const registryTools = ['search_skills', 'load_skill', 'load_resource', 'execute_resource']
    const supported = [...registryTools, 'terminal', 'read_file', 'write_file']
    const selected = process.env.HARNESS_TOOLS === undefined
        ? registryTools
        : JSON.parse(process.env.HARNESS_TOOLS)
    if (!Array.isArray(selected) ||
        !selected.every((tool) => typeof tool === 'string' && supported.includes(tool)) ||
        new Set(selected).size !== selected.length) {
        throw new Error('HARNESS_TOOLS 必须包含互不重复且受支持的工具名称')
    }
    if (selected.length === 0) return

    const bridge = process.env.HARNESS_BRIDGE_URL
    const token = process.env.HARNESS_BRIDGE_TOKEN
    const base = process.env.SKILL_REGISTRY_URL
    if (bridge && !token) throw new Error('缺少 HARNESS_BRIDGE_TOKEN')
    if (!bridge && selected.some((tool) => !registryTools.includes(tool))) {
        throw new Error('dsh 终端和文件工具需要绑定任务环境')
    }
    if (!bridge && !base) throw new Error('缺少 SKILL_REGISTRY_URL')
    // 独立调用仍在注册中心服务端执行；试验调用使用
    // Python 环境的 RegistryClient，并遵循本任务的技能范围限制。
    const exposedSkills = JSON.parse(process.env.EXPOSED_SKILLS ?? '[]')
    if (!Array.isArray(exposedSkills) ||
        !exposedSkills.every((skill) => typeof skill === 'string' && skill.trim().length > 0)) {
        throw new Error('EXPOSED_SKILLS 必须为技能名称的 JSON 数组')
    }

    function describeError(error: any, seen = new Set<any>()): any {
        if (error === null || typeof error !== 'object') return String(error)
        if (seen.has(error)) return '[circular error reference]'
        seen.add(error)
        const details: Record<string, any> = {}
        for (const key of ['name', 'message', 'code', 'stack', 'errno', 'syscall', 'address', 'port']) {
            if (error[key] !== undefined) details[key] = String(error[key])
        }
        if (error.cause !== undefined) details.cause = describeError(error.cause, seen)
        if (Array.isArray(error.errors)) details.errors = error.errors.map((item: any) => describeError(item, seen))
        return details
    }

    async function invoke(tool: string, args: Record<string, any>, signal: AbortSignal): Promise<string> {
        const requestSignal = !bridge && tool !== 'execute_resource'
            ? AbortSignal.any([signal, AbortSignal.timeout(30_000)]) : signal
        try {
            return await invokeRequest(tool, args, requestSignal)
        } catch (error) {
            // dsh 可能仅序列化 Error.message，因此也将异常原因链
            // 和实际请求取消信号的原因包含在该消息中。
            const details = {
                tool, error: describeError(error), aborted: requestSignal.aborted,
                abort_reason: requestSignal.aborted ? describeError(requestSignal.reason) : null,
            }
            throw new Error(JSON.stringify(details), { cause: error })
        }
    }

    async function invokeRequest(tool: string, args: Record<string, any>, signal: AbortSignal): Promise<string> {
        if (bridge) {
            const response = await fetch(bridge, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                body: JSON.stringify({ name: tool, arguments: args }),
                signal,
            })
            const result = await response.json()
            if (!response.ok) {
                const message = result.error ?? `工具桥接接口 HTTP ${response.status}`
                throw new Error(message + (result.traceback ? '\n' + result.traceback : ''))
            }
            if (typeof result.text !== 'string' || typeof result.is_error !== 'boolean') {
                throw new Error('工具桥接接口响应无效')
            }
            if (result.is_error) throw new Error(result.text)
            return result.text
        }

        const url = new URL('/' + tool, base)
        if (tool === 'execute_resource') {
            if (!args.skill_name.trim() || !args.relative_path.trim()) {
                throw new Error('skill_name 和 relative_path 不能为空')
            }
            if (args.timeout != null && (!Number.isFinite(args.timeout) || args.timeout <= 0)) {
                throw new Error('timeout 必须为有限正数')
            }
            const response = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    skill_name: args.skill_name, relative_path: args.relative_path,
                    arguments: args.arguments ?? [],
                    ...(args.timeout != null ? { timeout: args.timeout } : {}),
                }),
                signal,
            })
            const result = await response.text()
            if (!response.ok) throw new Error(`execute_resource 请求失败，HTTP ${response.status}: ${result}`)
            return result
        }
        if (tool === 'search_skills') {
            const response = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ query: args.query ?? '', exposed_skills: exposedSkills }),
                signal,
            })
            const result = await response.text()
            if (!response.ok) throw new Error(`search_skills 请求失败，HTTP ${response.status}: ${result}`)
            return result
        }
        url.searchParams.set('skill_name', args.name)
        if (tool === 'load_resource') url.searchParams.set('relative_path', args.relative_path)
        const response = await fetch(url, { signal })
        if (!response.ok) throw new Error(`注册中心请求失败，HTTP ${response.status}`)
        return JSON.stringify(await response.json())
    }

    const definitions: Record<string, any> = {
        search_skills: {
            description: 'Search the skill registry and return skill summaries without loading ' +
                'full instructions. Use load_skill to load a selected skill.',
            parameters: {
                query: { type: 'string', description: 'Search query; defaults to an empty string' },
                exposed_skills: {
                    type: 'array', items: { type: 'string' },
                    description: 'Compatibility parameter. Skill visibility is controlled by the caller, ' +
                        'using the task environment or EXPOSED_SKILLS.',
                },
            },
        },
        load_skill: {
            description: "Load a skill's instructions and resource list by name.",
            parameters: {
                name: { type: 'string', required: true,
                    description: 'Skill name from the available skills catalog' },
            },
        },
        load_resource: {
            description: 'Read a resource belonging to a skill.',
            parameters: {
                name: { type: 'string', required: true, description: 'Name of the skill owning the resource' },
                relative_path: { type: 'string', required: true,
                    description: 'Resource path relative to the skill root' },
            },
        },
        execute_resource: {
            description: 'Execute a script or executable resource belonging to a skill. ' +
                'Pass command-line arguments as separate array elements. ' + (bridge
                    ? "Resources are downloaded from the registry and executed in this task's " +
                      'environment. Relative input/output paths use its working directory; ' +
                      'write persistent outputs outside the temporary skill directory.'
                    : 'Execution takes place in the environment managed by the registry server.'),
            parameters: {
                skill_name: { type: 'string', required: true, description: 'Name of the skill owning the resource' },
                relative_path: { type: 'string', required: true,
                    description: 'Resource path relative to the skill root' },
                arguments: { type: 'array', items: { type: 'string' },
                    description: 'Command-line arguments as separate array elements; defaults to []' },
                timeout: { type: 'number',
                    description: 'Execution timeout in seconds; omit for the execution backend default' },
            },
        },
        terminal: {
            description: "Run Bash commands in this task's isolated Linux container. " +
                'Use this tool to inspect, read, edit files and execute programs. ' +
                'Each invocation starts a new shell; use cwd or absolute paths.',
            parameters: {
                command: { type: 'string', required: true,
                    description: 'Bash command executed inside the task container' },
                cwd: { type: 'string', description: 'Absolute Linux working directory; omit for image WORKDIR' },
                timeout_sec: { type: 'number',
                    description: 'Execution timeout in seconds; defaults to 120' },
            },
        },
        read_file: {
            description: 'Read numbered lines from a UTF-8 file in the task container. ' +
                'Relative paths use image WORKDIR; page with start_line and max_lines.',
            parameters: {
                path: { type: 'string', required: true,
                    description: 'UTF-8 file path inside the task container' },
                start_line: { type: 'integer',
                    description: 'First line to read, numbered from 1; defaults to 1' },
                max_lines: { type: 'integer',
                    description: 'Maximum lines to read, between 1 and 2000; defaults to 200' },
            },
        },
        write_file: {
            description: 'Write a complete UTF-8 file in the task container, creating parent directories. ' +
                'Overwrites existing contents. Relative paths use image WORKDIR.',
            parameters: {
                path: { type: 'string', required: true,
                    description: 'UTF-8 file path inside the task container' },
                content: { type: 'string', required: true,
                    description: 'Complete new file contents; overwrites an existing file' },
            },
        },
    }

    for (const tool of selected) {
        ctx.tools.register(defineTool({
            name: tool,
            ...definitions[tool],
            output: {
                schema: { type: 'string' },
                render: (_args, value) => [{ type: 'text', text: value }],
            },
            async execute(args, exec) {
                return invoke(tool, args, exec.signal)
            },
        }))
    }
}
