import os

from openhands.sdk import LLM, Agent, Conversation

from tools.openhands.register_tools import get_registry_tools

from litellm.types.utils import Usage
from openhands.sdk.llm.utils.telemetry import Telemetry


def _compatible_cache_buckets(usage) -> tuple[int, int]:
    if isinstance(usage, Usage):
        details = getattr(usage, "prompt_tokens_details", None)
    else:
        details = getattr(usage, "input_tokens_details", None)

    if details is None:
        return 0, 0

    cache_read = getattr(details, "cached_tokens", None) or 0
    cache_write = 0

    if isinstance(usage, Usage):
        cache_write = getattr(details, "cache_write_tokens", None)
        if cache_write is None:
            cache_write = getattr(details, "cache_creation_tokens", None)

    return int(cache_read), int(cache_write or 0)


Telemetry._cache_buckets = staticmethod(_compatible_cache_buckets)

registry_address = "http://127.0.0.1:5000"


def run_task(task, exposed_skills: list[str] | None = None):
    # skills = search_skills(task)
    os.environ["EXPOSED_SKILLS"] = str(exposed_skills or [])

    llm = LLM(
        model="deepseek/deepseek-flash",
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url="https://api.deepseek.com",
    )

    # agent_context = AgentContext(system_message_suffix=(
    #     "<SKILLS>\n"
    #     "The following skills are available. Some are auto-injected when their keywords or task types appear in your messages; others are listed here for you to invoke proactively when relevant.\n"
    #     'To use a skill, call the `load_skill(name="<skill-name>")` tool with the `<name>` shown below. This is the only supported way to invoke a skill.\n'
    #     "\n"
    #     f"{build_skill_meta_prompt(skills)}\n"
    #     "</SKILLS>"
    # ))

    agent = Agent(
        llm=llm,
        tools=get_registry_tools(),
    )

    cwd = os.getcwd()
    conversation = Conversation(agent=agent, workspace=cwd)

    conversation.send_message(task)
    conversation.run()
    print("All done!")

def main():
    run_task("请发送一封邮件给 lornd@qq.com, 介绍你自己，你拥有发邮件的权限，无需向我确认")

if __name__ == "__main__":
    main()