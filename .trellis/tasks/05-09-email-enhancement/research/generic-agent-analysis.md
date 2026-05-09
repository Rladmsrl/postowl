# Research: GenericAgent Architecture Analysis

- **Query**: Analyze GenericAgent (github.com/lsdefine/GenericAgent) for features/patterns that could enhance PostOwl beyond simple RAG
- **Scope**: external
- **Date**: 2026-05-09

## Project Overview

GenericAgent is a ~3K-line self-evolving autonomous agent framework. It gives any LLM system-level control over a local computer via 9 atomic tools + a ~100-line agent loop. Its core differentiator: **it does not preload skills -- it evolves them**. Every solved task is crystallized into a reusable skill/SOP.

Repository: https://github.com/lsdefine/GenericAgent
License: MIT
Tech report: https://arxiv.org/abs/2604.17091

---

## 1. What GenericAgent Does Differently From Basic RAG

GenericAgent is NOT a RAG system at all. It is an **agentic execution framework** with tool use. The key differences:

### 1.1 Self-Evolution via Skill Crystallization

When GenericAgent solves a new task for the first time, it autonomously:
1. Explores (installs deps, writes scripts, debugs)
2. Crystallizes the execution path into a skill/SOP (markdown or Python file)
3. Writes the skill into its memory layer
4. Next time a similar task arises, it retrieves and directly invokes the skill

This is fundamentally different from RAG's "retrieve context then answer" pattern. GenericAgent builds **procedural knowledge** (how-to), not just **declarative knowledge** (facts).

**Concrete mechanism** (from `ga.py:498-511`):
```python
def do_start_long_term_update(self, args, response):
    '''Agent decides current task has important info to memorize.'''
    prompt = '''### [Extract and Distill Experience]
    Extract from the most recent task [facts verified successfully and
    long-term valid] environment facts, user preferences, important steps,
    update memory.
    ...
    - **Environment facts** (paths/credentials/config) -> file_patch update L2, sync L1
    - **Complex task experience** (key pitfalls/prerequisites/important steps)
      -> L3 concise SOP (only record core points that tripped you up)
    **Forbidden**: temp variables, specific reasoning process, unverified info,
    common knowledge, easily reproducible details
    '''
```

### 1.2 Token Efficiency via Layered Memory (< 30K context)

GenericAgent operates in under 30K tokens of context, while other agents use 200K-1M. It achieves this through a 5-layer memory hierarchy:

| Layer | Name | Content | Size Constraint |
|-------|------|---------|-----------------|
| L0 | Meta Rules | Core behavioral rules, system constraints | Fixed, small |
| L1 | Insight Index | Minimal routing index: scene keyword -> memory location | Hard limit: <= 30 lines |
| L2 | Global Facts | Stable knowledge: paths, credentials, configs | Grows, but structured |
| L3 | Task Skills/SOPs | Reusable workflows for specific task types | Per-file, concise |
| L4 | Session Archive | Compressed records from finished sessions | Zip-archived |

**Key insight for PostOwl**: Instead of stuffing everything into context (like RAG does), GenericAgent uses a **pointer-based** system. L1 is just an index that tells the agent "this capability exists, here's how to find the details." The agent then reads the full SOP/fact only when needed.

**L1 Index Format** (from `assets/insight_fixed_structure_en.txt`):
```
Facts(L2): ../memory/global_mem.txt | CodeRoot: ../ | SOPs(L3): ../memory/*.md or *.py
L1 Insight is a minimal index; sync L1 when L2/L3 changes; keep index minimal.

[CONSTITUTION]
1. Ask before modifying own source code
2. Check memory before decisions; always use existing SOPs/utils
3. Execute step by step, control granularity, limit blast radius
4. Key/secret files: reference only, never read or move
5. Read META-SOP to verify before writing any memory
```

---

## 2. Agent/Tool-Use Patterns

### 2.1 The Agent Loop (~100 lines)

The core loop in `agent_loop.py:42-99` is remarkably simple:

```python
def agent_runner_loop(client, system_prompt, user_input, handler,
                      tools_schema, max_turns=40, verbose=True):
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_input}
    ]
    turn = 0
    while turn < handler.max_turns:
        turn += 1
        response = client.chat(messages=messages, tools=tools_schema)

        if not response.tool_calls:
            tool_calls = [{'tool_name': 'no_tool', 'args': {}}]
        else:
            tool_calls = [parse each tool call]

        for tc in tool_calls:
            outcome = handler.dispatch(tool_name, args, response)
            if outcome.should_exit: break
            if not outcome.next_prompt: break  # CURRENT_TASK_DONE

        messages = [{"role": "user", "content": next_prompt,
                     "tool_results": tool_results}]
```

Pattern: **Single-message history sliding window**. After each turn, only the new user message (containing tool results + working memory anchor) is sent. Full history is maintained inside the LLM session object, not rebuilt each turn.

### 2.2 Nine Atomic Tools

| Tool | Purpose | PostOwl Relevance |
|------|---------|-------------------|
| `code_run` | Execute Python/bash | Could run email processing scripts dynamically |
| `file_read` | Read files with keyword search, line ranges | Memory/knowledge retrieval |
| `file_write` | Write/append/prepend files | Persist knowledge |
| `file_patch` | Replace unique text block in file | Update existing knowledge |
| `web_scan` | Get simplified HTML from browser | Could scan email-linked web content |
| `web_execute_js` | Control browser via JS | N/A for email agent |
| `ask_user` | Human-in-the-loop confirmation | Useful for ambiguous email actions |
| `update_working_checkpoint` | Short-term working notepad | Maintain context across email processing |
| `start_long_term_update` | Trigger memory distillation | Learn from email patterns |

### 2.3 The `no_tool` Pseudo-Tool Pattern

When the LLM responds without calling any tool, GenericAgent auto-triggers a `do_no_tool` handler (`ga.py:448-496`) that:
1. Detects empty/incomplete responses and retries
2. Catches code blocks without tool calls (forces the agent to actually execute)
3. Validates plan-mode completion claims require verification
4. After 3 empty responses, force-exits

This prevents the agent from "talking about doing things" instead of actually doing them.

### 2.4 Working Memory Anchor Prompt

Every turn, the agent receives an "anchor prompt" (`ga.py:529-541`) injected as the next user message:

```python
def _get_anchor_prompt(self, skip=False):
    h = self.history_info
    earlier = fold_earlier(h[:-30])  # compress old history
    h_str = "\n".join(h[-30:])       # recent 30 lines
    prompt = f"\n### [WORKING MEMORY]\n{earlier}<history>\n{h_str}\n</history>"
    prompt += f"\nCurrent turn: {self.current_turn}\n"
    if self.working.get('key_info'):
        prompt += f"\n<key_info>{self.working['key_info']}</key_info>"
    if self.working.get('related_sop'):
        prompt += f"\nRe-read {self.working['related_sop']} if unclear"
    return prompt
```

This is a compact "state of the world" injected each turn, much more efficient than full conversation replay. The `key_info` field acts as a user-controlled scratchpad that persists across turns.

---

## 3. Memory/Conversation Management

### 3.1 History Compression

The `<summary>` tag system is central. Every turn, the agent MUST output a `<summary>` tag with a one-line (<30 word) snapshot of what happened. These summaries become the working memory history:

```
[USER]: Monitor stocks and alert me
[Agent] Read SOP, found stock monitoring template
[Agent] Installed mootdx, configured data source
[Agent] Built selection flow with EXPMA golden cross filter
[Agent] Set up cron job, saved skill
```

Older history is further compressed by the `_fold_earlier` method:
```python
def _fold_earlier(self, lines):
    # Groups consecutive agent turns into summary counts:
    # "[Agent] did X (3 turns)" instead of listing each
```

### 3.2 Context Window Trimming

`llmcore.py:33-63` implements aggressive context trimming:

```python
def compress_history_tags(messages, keep_recent=10, max_len=800):
    """Compress <thinking>/<tool_use>/<tool_result> tags in older messages."""
    # Every 5 calls, truncate older message content
    # <history>, <key_info>, <earlier_context> tags -> [...]
    # Tool results truncated to max_len chars

def trim_messages_history(history, context_win):
    # If total context > context_win * 3:
    #   1. Force-compress with keep_recent=4
    #   2. Pop oldest messages until under 60% of limit
    #   3. Sanitize leading user messages (clean orphaned tool_results)
```

### 3.3 L4 Session Archive (Long-Term Memory)

`memory/L4_raw_sessions/compress_session.py` implements automated session archiving:
1. Raw model response logs are compressed (strip system prompts, assistant echoes)
2. `<history>` blocks are extracted and merged (sliding window dedup)
3. Compressed sessions go into monthly zip archives
4. All history lines go into `all_histories.txt` for full-text search
5. Triggered by scheduler every 12 hours

This means the agent can recall what it did weeks ago through L4 search, without keeping it in context.

### 3.4 Cross-Session Key Info Persistence

When starting a new conversation (`agentmain.py:139-143`):
```python
if self.handler and 'key_info' in self.handler.working:
    handler.working['key_info'] = ki
    handler.working['passed_sessions'] = ps + 1
    if ps > 0:
        handler.working['key_info'] += (
            f'\n[SYSTEM] This key_info was set {ps} conversations ago, '
            f'if on new task, update or clear working memory.\n')
```

Working memory persists across conversations but with a staleness warning.

---

## 4. Prompt Engineering Techniques

### 4.1 System Prompt: "Physical-Level Omnipotent Executor"

The system prompt (`assets/sys_prompt_en.txt`) is remarkably short:
```
# Role: Physical-Level Omnipotent Executor
You have full physical access: file I/O, script execution, browser JS injection,
and system-level intervention. Never deflect with "can't do it" -- don't speculate,
use tools to probe.

## Action Principles
Before each tool call, reason: current phase, whether last result met expectations,
and next strategy and <summary> in reply text of each turn.
- Probe first: on failure, gather info, store key findings in working memory,
  then decide to retry or pivot.
- Failure escalation: 1st fail -> read error; 2nd -> probe environment;
  3rd -> deep analysis then switch approach or ask user.
```

Key technique: **failure escalation ladder**. Don't just retry -- escalate the debugging strategy.

### 4.2 Interaction Protocol (Tool Calling)

For non-native tool-calling models, GenericAgent uses a structured XML protocol (`llmcore.py:760-785`):
```
### Interaction Protocol (must follow strictly)
1. **Think**: Analyze in <thinking> tags
2. **Summarize**: Output minimal one-line physical snapshot in <summary>
3. **Act**: Output <tool_use> blocks

Format: <tool_use>{"name": "tool_name", "arguments": {...}}</tool_use>

### Tools (mounted, always in effect):
[full tools JSON schema]
```

After tools are sent once, subsequent turns get a compressed reminder:
```
### Tools: still active, **ready to call**. Protocol unchanged.
```

This saves significant tokens on multi-turn conversations.

### 4.3 Turn Escalation Warnings

`ga.py:557-565`:
```python
if turn % 65 == 0 and not _plan:
    next_prompt += f"\n\n[DANGER] Already on turn {turn}. Must summarize and
    ask_user, no more retries."
elif turn % 7 == 0:
    next_prompt += f"\n\n[DANGER] Already on turn {turn}. No useless retries.
    If no progress: 1. Probe physical boundaries 2. Request user help."
elif turn % 10 == 0:
    next_prompt += get_global_memory()  # Refresh memory every 10 turns
```

### 4.4 Memory Management Axioms

The memory management SOP (`memory/memory_management_sop.md`) contains critical axioms:

1. **No Execution, No Memory**: Only store information from successful tool call results. Never store guesses, plans, or unverified assumptions.
2. **Sanctity of Verified Data**: Never delete verified configurations during reorganization.
3. **No Volatile State**: Never store timestamps, session IDs, PIDs, or connection info.
4. **Minimum Sufficient Pointer**: Upper layers only keep the shortest identifier that can locate the lower layer.

---

## 5. Multi-Turn Conversation Handling

### 5.1 Subagent Delegation

GenericAgent can spawn subagents for parallel/delegated work (`memory/subagent.md`):

```bash
python agentmain.py --task {name} --input "short text" --llm_no N
```

Communication is file-based:
- `input.txt` -> initial task
- `output.txt` -> agent writes results (append, `[ROUND END]` marks completion)
- `reply.txt` -> write to continue conversation
- `_stop` -> signal to stop
- `_keyinfo` -> inject info into working memory
- `_intervene` -> append instructions to correct course

Three usage patterns:
1. **Test Mode**: Observe agent behavior, correct RULES/SOPs
2. **Map Mode**: Parallel processing of N independent subtasks (each gets its own context)
3. **Supervisor Mode**: A supervisor agent monitors a worker agent, intervening via `_intervene` files

### 5.2 Plan Mode for Complex Tasks

For tasks with 3+ steps or dependencies, the agent enters Plan Mode (`memory/plan_sop.md`):

```
Phase 1: Exploration (subagent probes environment)
Phase 2: Planning (write plan.md with checkboxed steps)
Phase 3: Execution (loop: read plan -> find first [ ] -> execute -> mark [v])
Phase 4: Verification (independent subagent does adversarial verification)
```

Plan.md format:
```markdown
<!-- EXECUTION PROTOCOL (read every turn)
1. file_read(plan.md), find first [ ] item
2. Step has SOP -> file_read that SOP
3. Execute step + mini verification
4. file_patch mark [ ] -> [v]+brief result, continue to next [ ]
5. All steps done -> terminal check: 0 [ ] remaining
-->
# Task Title
Requirements: one line | Constraints: key limits

## Execution Plan
1. [ ] Step 1 description
   SOP: xxx_sop.md
2. [D] Step 2 (delegate to subagent)
3. [P] Step 3 (parallel, use Map mode)
4. [?] Step 4 (conditional branch)

## Verification Checkpoint
N+1. [ ] **[VERIFY] Launch independent verification subagent**
```

### 5.3 Goal Mode (Budget-Based Autonomous Operation)

`reflect/goal_mode.py` implements continuous self-driven operation:
- User sets an objective + time budget (e.g., "optimize X for 3 hours")
- Agent is repeatedly woken up every 3 seconds
- Each wake-up gets: objective, elapsed time, remaining budget, turn count
- Rules: "Never say 'done, should I continue?' -- if budget remains, keep going"
- When budget expires, agent does a wrap-up round summarizing progress

---

## 6. Workflow/Pipeline Patterns Worth Borrowing

### 6.1 Reflect Mode Architecture

`agentmain.py:225-254` implements a generic "reflect" pattern:

```python
if args.reflect:
    spec = importlib.util.spec_from_file_location('reflect_script', args.reflect)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    while True:
        time.sleep(getattr(mod, 'INTERVAL', 5))
        task = mod.check()  # Returns prompt string or None
        if task is None: continue
        dq = agent.put_task(task, source='reflect')
        # ... wait for completion, call mod.on_done(result)
```

Any Python module with `INTERVAL`, `check()`, and optional `on_done(result)` can drive the agent. Current reflect modules:
- `scheduler.py`: Cron-like scheduled tasks (JSON config, cooldown-based)
- `autonomous.py`: Self-driven mode when user is away (30min idle trigger)
- `goal_mode.py`: Budget-based continuous improvement
- `agent_team_worker.py`: Multi-agent BBS-based task marketplace

### 6.2 Skill Search Engine

`memory/skill_search/skill_search/engine.py` implements a remote skill library:
- Environment detection (OS, shell, runtimes, installed tools)
- API-based skill search with relevance/quality scoring
- Each skill has metadata: category, tags, quality scores, safety ratings
- Hosted at Sophub (fudankw.cn) with a million+ skills

### 6.3 File-Based Inter-Agent Communication

All agent communication is file-based, not API-based:
- `_keyinfo` file: Inject information into working memory
- `_intervene` file: Append corrective instructions
- `_stop` file: Graceful shutdown signal
- `output.txt` / `reply.txt`: Request/response cycle

This is trivially simple and debuggable -- you can manually write these files to control the agent.

---

## Actionable Patterns for PostOwl

### Pattern A: Layered Memory for Email Knowledge

Instead of stuffing all email history into ChromaDB and retrieving top-K, consider a layered approach:
- **L1 (Index)**: "User has 3 active projects: X, Y, Z. Contacts: {key contacts with roles}. Rules: {user preferences}"
- **L2 (Facts)**: Structured contact database, project details, recurring email patterns
- **L3 (Procedures)**: SOPs for handling specific email types (e.g., "invoice from Vendor X: extract amount, forward to accounting, reply with confirmation")
- **L4 (Archive)**: Compressed email session logs for long-term recall

### Pattern B: Self-Evolving Email Rules

When PostOwl handles a new type of email interaction (e.g., user manually replies to a type of email), it could crystallize the pattern:
```
[First time] User: "Forward all invoices from X to accounting@"
[Agent executes, then stores as SOP]
[Next time] Invoice from X arrives -> auto-forward (skill recall)
```

### Pattern C: Working Memory Checkpoint for Multi-Email Processing

When processing a batch of emails, maintain a working memory checkpoint:
```
key_info: "Processing batch of 47 emails. 23 done. High-priority: email from CEO about Q3 budget (ID: xxx). Reminder: user prefers Chinese summaries. Current filter: last 7 days."
```

### Pattern D: Failure Escalation in Email Processing

Instead of silently failing on parse errors:
1. First failure: Log error, try alternative parser
2. Second failure: Probe email structure (headers, encoding, MIME type)
3. Third failure: Flag for user review with diagnostic info

### Pattern E: Reflect-Based Scheduler for Email Monitoring

The reflect pattern could replace PostOwl's APScheduler with something more flexible:
```python
# email_monitor.py
INTERVAL = 300  # check every 5 minutes

def check():
    new_count = count_new_emails()
    if new_count > 0:
        return f"[Email] {new_count} new emails. Fetch, classify, summarize."
    return None

def on_done(result):
    # Send Telegram notification if high-priority emails found
    notify_if_important(result)
```

### Pattern F: Summary-as-History for Email Conversations

Instead of full email thread in context, use GenericAgent's summary pattern:
```
[Email] CEO -> User: Q3 budget request (priority: high, action: respond by Friday)
[Email] User -> CEO: Submitted Q3 budget draft
[Email] CEO -> User: Approved with minor changes to marketing line item
```

Each email reduced to one summary line. Full content available via retrieval only when needed.

---

## Key Files Reference

| File | Description |
|------|-------------|
| `/tmp/GenericAgent/agent_loop.py` | Core agent loop (~100 lines), BaseHandler, StepOutcome |
| `/tmp/GenericAgent/ga.py` | GenericAgentHandler -- all 9 tool implementations + working memory |
| `/tmp/GenericAgent/llmcore.py` | LLM session management, context trimming, multi-provider support |
| `/tmp/GenericAgent/agentmain.py` | GenericAgent orchestrator, reflect mode, task queueing |
| `/tmp/GenericAgent/assets/sys_prompt_en.txt` | System prompt (7 lines) |
| `/tmp/GenericAgent/assets/tools_schema.json` | Tool definitions (9 tools) |
| `/tmp/GenericAgent/memory/memory_management_sop.md` | 5-layer memory architecture spec |
| `/tmp/GenericAgent/memory/plan_sop.md` | Plan mode: explore -> plan -> execute -> verify |
| `/tmp/GenericAgent/memory/subagent.md` | Subagent delegation protocol |
| `/tmp/GenericAgent/memory/memory_cleanup_sop.md` | Memory cleanup/compression rules |
| `/tmp/GenericAgent/reflect/scheduler.py` | Cron-like scheduled task execution |
| `/tmp/GenericAgent/reflect/goal_mode.py` | Budget-based autonomous operation |
| `/tmp/GenericAgent/reflect/autonomous.py` | Self-driven idle-time mode |
| `/tmp/GenericAgent/memory/L4_raw_sessions/compress_session.py` | Session archiving/compression |
| `/tmp/GenericAgent/memory/skill_search/skill_search/engine.py` | Skill library search client |
| `/tmp/GenericAgent/frontends/tgapp.py` | Telegram bot frontend |

## Caveats / Limitations

1. GenericAgent is designed for **desktop automation**, not email processing. Its browser/keyboard tools are irrelevant to PostOwl.
2. The self-evolution mechanism relies on file-system-based memory, which works for a single-user desktop agent but may need adaptation for a multi-account email system.
3. The skill search engine requires an external API server (Sophub) -- PostOwl would need a local equivalent.
4. GenericAgent's context efficiency comes partly from its minimal system prompt (7 lines) -- PostOwl's email-specific prompts will necessarily be longer.
5. The technical report (arXiv:2604.17091) discusses "Contextual Information Density Maximization" but the PDF was not analyzed in this research.
