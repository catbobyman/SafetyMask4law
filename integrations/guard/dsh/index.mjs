// Mask guard for DeepSeek Harness (dsh). dsh has no command hooks, so this is a cordis plugin: it hands
// each typed prompt and each file, search or shell tool call to `python -m mask guard hook` as a
// Claude Code hook event and applies the answer. `mask.cmd install dsh` adds it to the dsh home patch
// with `config: {python, root}`. The same install points dsh's DeepSeek route at the Mask gateway
// (a baseURL row in the same patch block), so the plugin also starts the gateway whenever dsh starts.
import { spawn } from 'node:child_process'

export const name = 'mask-guard'
export const inject = ['tools', 'workingDirectory']

function ask(config, event) {
  return new Promise((resolve, reject) => {
    const child = spawn(config.python, ['-m', 'mask', 'guard', 'hook'],
      { cwd: config.root, windowsHide: true, env: { ...process.env, PYTHONIOENCODING: 'utf-8' } })
    let out = '', err = ''
    child.stdout.setEncoding('utf8').on('data', (d) => { out += d })
    child.stderr.setEncoding('utf8').on('data', (d) => { err += d })
    child.on('error', reject)
    // A check that cannot run rejects, which makes dsh refuse the prompt or the tool call.
    child.on('close', (code) => code === 0
      ? resolve(out.trim() ? JSON.parse(out) : null)
      : reject(new Error(err.trim() || `Mask 拦截检查失败（退出码 ${code}）`)))
    child.stdin.end(JSON.stringify(event))
  })
}

export function apply(ctx, config) {
  // `gateway start` returns at once when the gateway is already listening. If it cannot be started, dsh's
  // model requests fail to connect, which shows; the error is printed, not thrown, so dsh still boots.
  spawn(config.python, ['-m', 'mask', 'gateway', 'start'], { cwd: config.root, windowsHide: true, detached: true, stdio: 'ignore' })
    .on('error', (error) => console.error(`Mask 网关未能启动：${error.message}`)).unref()

  ctx.on('tools/pre-execute', async (exec, next) => {
    const input = exec.arguments ?? {}
    const search = exec.name === 'grep' || exec.name === 'glob'
    if (!exec.agent || !(search || ['file_path', 'path', 'command'].some((k) => typeof input[k] === 'string'))) return next()
    const answer = await ask(config, {
      hook_event_name: 'PreToolUse', cwd: ctx.workingDirectory.get(exec.agent.session), tool_name: exec.name, tool_input: input,
    })
    const reason = answer?.hookSpecificOutput?.permissionDecisionReason
    return reason ? { kind: 'deny', reason } : next()
  }, { prepend: true })

  ctx.on('agent/pre-step', async (payload, next) => {
    // Runs on every step; only what the user typed is checked here.
    const prompt = payload.messages.filter((m) => m.source?.kind === 'user')
      .flatMap((m) => m.content).filter((b) => b.type === 'text').map((b) => b.text).join('\n')
    if (prompt) {
      const answer = await ask(config, {
        hook_event_name: 'UserPromptSubmit', cwd: ctx.workingDirectory.get(payload.agent.session), prompt,
      })
      // Throwing ends the turn before the model is called and shows the message; a plain reject shows nothing.
      if (answer?.decision === 'block') throw new Error(answer.reason)
    }
    return next()
  }, { prepend: true })
}
